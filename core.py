"""PDF Studio - core document operations (no GUI).

Everything that reads, edits or converts documents lives here so it can be
tested without a screen and reused by the GUI.
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from typing import Callable, Iterable, List, Optional, Sequence

import pymupdf as fitz

Progress = Optional[Callable[[int, int], None]]  # (done, total)

OPENABLE_EXT = (".pdf", ".xps", ".oxps", ".epub", ".cbz", ".fb2", ".mobi",
                ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
                ".webp", ".svg", ".txt")

EXPORT_FORMATS = {
    # key: (label, file filter or None for folder output)
    "pdf": ("PDF document (*.pdf)", "*.pdf"),
    "pdf_compressed": ("Compressed PDF (*.pdf)", "*.pdf"),
    "docx": ("Word document (*.docx)", "*.docx"),
    "xlsx": ("Excel workbook - tables (*.xlsx)", "*.xlsx"),
    "pptx": ("PowerPoint - one slide per page (*.pptx)", "*.pptx"),
    "txt": ("Plain text (*.txt)", "*.txt"),
    "html": ("Web page (*.html)", "*.html"),
    "md": ("Markdown (*.md)", "*.md"),
    "rtf": ("Rich text (*.rtf)", "*.rtf"),
    "json": ("Structured text JSON (*.json)", "*.json"),
    "tiff": ("Multi-page TIFF image (*.tiff)", "*.tiff"),
    "png": ("PNG images - one per page", None),
    "jpg": ("JPG images - one per page", None),
    "svg": ("SVG vector images - one per page", None),
    "csv": ("CSV files - one per table", None),
    "images": ("Extract embedded images", None),
}


# --------------------------------------------------------------------------- helpers
def _tick(progress: Progress, done: int, total: int) -> None:
    if progress:
        progress(done, total)


def parse_ranges(text: str, page_count: int) -> List[int]:
    """'1-3, 5, 8-' -> zero-based page indexes. Empty/'all' -> all pages."""
    text = (text or "").strip().lower()
    if not text or text == "all":
        return list(range(page_count))
    pages: List[int] = []
    for part in re.split(r"[,;\s]+", text):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else page_count
        else:
            start = end = int(part)
        if start > end:
            start, end = end, start
        for p in range(max(1, start), min(page_count, end) + 1):
            if p - 1 not in pages:
                pages.append(p - 1)
    if not pages:
        raise ValueError("No valid pages in range '%s'" % text)
    return pages


def open_document(path: str, password: Optional[str] = None) -> fitz.Document:
    doc = fitz.open(path)
    if doc.needs_pass:
        if password is None or not doc.authenticate(password):
            doc.close()
            raise PermissionError("Password required")
    if not doc.is_pdf:
        # convert XPS/EPUB/images etc. to PDF so every edit tool works
        pdf_bytes = doc.convert_to_pdf()
        doc.close()
        doc = fitz.open("pdf", pdf_bytes)
    return doc


def subset(doc: fitz.Document, pages: Sequence[int]) -> fitz.Document:
    out = fitz.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p, to_page=p)
    return out


def _base(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or "document"


# --------------------------------------------------------------------------- export
def export(doc: fitz.Document, fmt: str, target: str, pages: Sequence[int],
           dpi: int = 150, jpg_quality: int = 90, progress: Progress = None,
           name: str = "document") -> List[str]:
    """Export `pages` of `doc` to `fmt`. `target` is a file path, or a folder
    for multi-file formats. Returns the list of files written."""
    fn = {
        "pdf": export_pdf, "pdf_compressed": export_pdf_compressed,
        "docx": export_docx, "xlsx": export_xlsx, "pptx": export_pptx,
        "txt": export_txt, "html": export_html, "md": export_markdown,
        "rtf": export_rtf, "json": export_json, "tiff": export_tiff,
        "png": export_images, "jpg": export_images, "svg": export_svg,
        "csv": export_csv, "images": extract_images,
    }[fmt]
    kwargs = dict(pages=pages, progress=progress)
    if fmt in ("png", "jpg"):
        kwargs.update(fmt=fmt, dpi=dpi, quality=jpg_quality, name=name)
    elif fmt in ("tiff", "pptx"):
        kwargs.update(dpi=dpi)
    elif fmt in ("svg", "csv", "images"):
        kwargs.update(name=name)
    elif fmt == "pdf_compressed":
        kwargs.update(dpi=dpi, quality=jpg_quality)
    return fn(doc, target, **kwargs)


def export_pdf(doc, path, pages, progress=None):
    out = subset(doc, pages) if len(pages) != doc.page_count else doc
    out.save(path, garbage=3, deflate=True)
    _tick(progress, 1, 1)
    return [path]


def export_pdf_compressed(doc, path, pages, dpi=150, quality=75, progress=None):
    out = subset(doc, pages)
    _tick(progress, 0, 2)
    try:
        out.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality,
                           lossy=True, lossless=True, bitonal=True, color=True,
                           gray=True, set_to_gray=False)
    except Exception:
        pass  # older MuPDF: fall back to structural compression only
    try:
        out.subset_fonts()
    except Exception:
        pass
    out.save(path, garbage=4, deflate=True, deflate_images=True,
             deflate_fonts=True, clean=True, use_objstms=1)
    _tick(progress, 2, 2)
    return [path]


def export_images(doc, folder, pages, fmt="png", dpi=150, quality=90,
                  progress=None, name="document"):
    os.makedirs(folder, exist_ok=True)
    files = []
    width = len(str(doc.page_count))
    for i, p in enumerate(pages):
        pix = doc[p].get_pixmap(dpi=dpi, alpha=False)
        f = os.path.join(folder, f"{name}_page{str(p + 1).zfill(width)}.{fmt}")
        if fmt == "jpg":
            pix.save(f, jpg_quality=quality)
        else:
            pix.save(f)
        files.append(f)
        _tick(progress, i + 1, len(pages))
    return files


def export_tiff(doc, path, pages, dpi=150, progress=None):
    from PIL import Image
    frames = []
    for i, p in enumerate(pages):
        pix = doc[p].get_pixmap(dpi=dpi, alpha=False)
        frames.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        _tick(progress, i + 1, len(pages))
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   compression="tiff_deflate", dpi=(dpi, dpi))
    return [path]


def export_svg(doc, folder, pages, progress=None, name="document"):
    os.makedirs(folder, exist_ok=True)
    files = []
    for i, p in enumerate(pages):
        f = os.path.join(folder, f"{name}_page{p + 1}.svg")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(doc[p].get_svg_image(text_as_path=False))
        files.append(f)
        _tick(progress, i + 1, len(pages))
    return files


def export_txt(doc, path, pages, progress=None):
    with open(path, "w", encoding="utf-8") as fh:
        for i, p in enumerate(pages):
            if i:
                fh.write("\n\f\n")
            fh.write(doc[p].get_text("text", sort=True))
            _tick(progress, i + 1, len(pages))
    return [path]


def export_html(doc, path, pages, progress=None):
    parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
             f"<title>{_esc(doc.metadata.get('title') or 'Document')}</title>",
             "<style>body{background:#e9e9ee;margin:0;padding:20px}"
             "div[id^=page]{background:#fff;margin:0 auto 20px;"
             "box-shadow:0 2px 8px rgba(0,0,0,.2)}</style></head><body>"]
    for i, p in enumerate(pages):
        parts.append(doc[p].get_text("html"))
        _tick(progress, i + 1, len(pages))
    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return [path]


def export_json(doc, path, pages, progress=None):
    import json
    data = {"metadata": doc.metadata, "pages": []}
    for i, p in enumerate(pages):
        d = doc[p].get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
        blocks = []
        for b in d["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    blocks.append({"text": s["text"], "bbox": [round(v, 2) for v in s["bbox"]],
                                   "font": s["font"], "size": round(s["size"], 2),
                                   "color": "#%06x" % s["color"]})
        data["pages"].append({"number": p + 1, "width": d["width"],
                              "height": d["height"], "spans": blocks})
        _tick(progress, i + 1, len(pages))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return [path]


def export_markdown(doc, path, pages, progress=None):
    _tick(progress, 0, 1)
    try:
        import pymupdf4llm
        try:
            md = pymupdf4llm.to_markdown(doc, pages=list(pages), show_progress=False, use_ocr=False)
        except TypeError:
            md = pymupdf4llm.to_markdown(doc, pages=list(pages), show_progress=False)
    except Exception:
        md = _simple_markdown(doc, pages)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    _tick(progress, 1, 1)
    return [path]


def _simple_markdown(doc, pages):
    sizes = []
    for p in pages:
        for b in doc[p].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if s["text"].strip():
                        sizes.append(round(s["size"]))
    body = max(set(sizes), key=sizes.count) if sizes else 11
    out = []
    for p in pages:
        for b in doc[p].get_text("dict")["blocks"]:
            text = " ".join(s["text"] for l in b.get("lines", []) for s in l["spans"]).strip()
            if not text:
                continue
            size = max((s["size"] for l in b.get("lines", []) for s in l["spans"]), default=body)
            if size >= body * 1.6:
                out.append("# " + text)
            elif size >= body * 1.25:
                out.append("## " + text)
            else:
                out.append(text)
        out.append("\n---\n")
    return "\n\n".join(out)


def export_rtf(doc, path, pages, progress=None):
    def rtf_escape(s):
        r = []
        for ch in s:
            o = ord(ch)
            if ch in "\\{}":
                r.append("\\" + ch)
            elif ch == "\n":
                r.append("\\par\n")
            elif o > 127:
                r.append("\\u%d?" % (o if o < 32768 else o - 65536))
            else:
                r.append(ch)
        return "".join(r)
    parts = [r"{\rtf1\ansi\deff0{\fonttbl{\f0 Calibri;}}\fs22 "]
    for i, p in enumerate(pages):
        if i:
            parts.append(r"\page ")
        parts.append(rtf_escape(doc[p].get_text("text", sort=True)))
        _tick(progress, i + 1, len(pages))
    parts.append("}")
    with open(path, "w", encoding="ascii") as fh:
        fh.write("".join(parts))
    return [path]


def _tables(doc, pages):
    """Yield (page_no, table_index, rows) for every table found."""
    for p in pages:
        try:
            tabs = doc[p].find_tables()
        except Exception:
            continue
        page = doc[p]
        for t_i, t in enumerate(tabs.tables):
            rows = []
            try:
                for row in t.rows:
                    rows.append([" ".join(page.get_textbox(fitz.Rect(c)).split()) if c else ""
                                 for c in row.cells])
            except Exception:
                rows = [[("" if c is None else str(c)) for c in r] for r in t.extract()]
            if rows:
                yield p, t_i, rows


def export_xlsx(doc, path, pages, progress=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    found = False
    for i, p in enumerate(pages):
        for _, t_i, rows in _tables(doc, [p]):
            found = True
            ws = wb.create_sheet(f"P{p + 1}_T{t_i + 1}"[:31])
            for r in rows:
                ws.append([_num(c) for c in r])
            for c in ws[1]:
                c.font = Font(bold=True)
                c.fill = PatternFill("solid", fgColor="DDE4F0")
            for col in ws.columns:
                w = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(60, max(8, w + 2))
        _tick(progress, i + 1, len(pages))
    if not found:
        # no ruled tables: put text lines into a sheet, split on wide gaps
        ws = wb.create_sheet("Text")
        for p in pages:
            ws.append([f"--- Page {p + 1} ---"])
            for line in _text_lines(doc[p]):
                ws.append([_num(c) for c in line])
    wb.save(path)
    return [path]


def _text_lines(page):
    """Rows of cells built from words, splitting cells on big horizontal gaps."""
    words = page.get_text("words", sort=True)
    rows, cur, last_y = [], [], None
    for w in words:
        y = round(w[3])
        if last_y is not None and abs(y - last_y) > 3:
            rows.append(cur)
            cur = []
        cur.append(w)
        last_y = y
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r.sort(key=lambda w: w[0])
        cells, cell, prev_x1 = [], [], None
        for w in r:
            if prev_x1 is not None and w[0] - prev_x1 > 12:
                cells.append(" ".join(cell))
                cell = []
            cell.append(w[4])
            prev_x1 = w[2]
        cells.append(" ".join(cell))
        out.append(cells)
    return out


def _num(v):
    if not isinstance(v, str):
        return v
    s = v.strip().replace(",", "")
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return v
    if re.fullmatch(r"-?\d*\.\d+", s):
        try:
            return float(s)
        except ValueError:
            return v
    return v


def export_csv(doc, folder, pages, progress=None, name="document"):
    os.makedirs(folder, exist_ok=True)
    files = []
    for p, t_i, rows in _tables(doc, pages):
        f = os.path.join(folder, f"{name}_page{p + 1}_table{t_i + 1}.csv")
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
        files.append(f)
    if not files:
        f = os.path.join(folder, f"{name}_text.csv")
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            wr = csv.writer(fh)
            for p in pages:
                wr.writerows(_text_lines(doc[p]))
        files.append(f)
    _tick(progress, 1, 1)
    return files


def export_pptx(doc, path, pages, dpi=150, progress=None):
    from pptx import Presentation
    from pptx.util import Emu
    prs = Presentation()
    first = doc[pages[0]].rect
    prs.slide_width = Emu(int(first.width * 12700))
    prs.slide_height = Emu(int(first.height * 12700))
    blank = prs.slide_layouts[6]
    for i, p in enumerate(pages):
        page = doc[p]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(io.BytesIO(pix.tobytes("png")), 0, 0,
                                 prs.slide_width, prs.slide_height)
        notes = page.get_text().strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes[:5000]
        _tick(progress, i + 1, len(pages))
    prs.save(path)
    return [path]


def export_docx(doc, path, pages, progress=None):
    from pdf2docx import Converter
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        subset(doc, pages).save(tmp.name)
        _tick(progress, 0, 1)
        cv = Converter(tmp.name)
        try:
            cv.convert(path, multi_processing=False)
        finally:
            cv.close()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    _tick(progress, 1, 1)
    return [path]


def extract_images(doc, folder, pages, progress=None, name="document"):
    os.makedirs(folder, exist_ok=True)
    files, seen = [], set()
    for i, p in enumerate(pages):
        for img in doc[p].get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if not info or not info.get("image"):
                continue
            ext = info.get("ext", "png")
            f = os.path.join(folder, f"{name}_p{p + 1}_img{xref}.{ext}")
            with open(f, "wb") as fh:
                fh.write(info["image"])
            files.append(f)
        _tick(progress, i + 1, len(pages))
    return files


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- create / combine
def files_to_pdf(paths: Iterable[str], out_path: str, progress: Progress = None) -> str:
    """Merge PDFs and convert images / XPS / EPUB etc. into one PDF."""
    paths = list(paths)
    out = fitz.open()
    for i, p in enumerate(paths):
        src = fitz.open(p)
        if src.is_pdf:
            out.insert_pdf(src)
        else:  # images, XPS, EPUB ... -> PDF pages
            out.insert_pdf(fitz.open("pdf", src.convert_to_pdf()))
        src.close()
        _tick(progress, i + 1, len(paths))
    out.save(out_path, garbage=3, deflate=True)
    out.close()
    return out_path


def split_pdf(doc: fitz.Document, folder: str, mode: str, value: str,
              name: str = "document") -> List[str]:
    """mode: 'every' (value = N pages per file), 'ranges' (value = '1-3;4-6'),
    'single' (one file per page)."""
    os.makedirs(folder, exist_ok=True)
    n = doc.page_count
    groups: List[List[int]] = []
    if mode == "single":
        groups = [[i] for i in range(n)]
    elif mode == "every":
        k = max(1, int(value))
        groups = [list(range(i, min(n, i + k))) for i in range(0, n, k)]
    else:
        for part in re.split(r"[;|]+", value):
            if part.strip():
                groups.append(parse_ranges(part, n))
    files = []
    for g_i, g in enumerate(groups):
        label = f"{g[0] + 1}" if len(g) == 1 else f"{g[0] + 1}-{g[-1] + 1}"
        f = os.path.join(folder, f"{name}_part{g_i + 1}_p{label}.pdf")
        subset(doc, g).save(f, garbage=3, deflate=True)
        files.append(f)
    return files


# --------------------------------------------------------------------------- edits
def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def add_watermark(doc, text, pages, fontsize=60, color="#c0392b", opacity=0.25,
                  angle=45, image_path: Optional[str] = None):
    rgb = hex_to_rgb(color)
    for p in pages:
        page = doc[p]
        r = page.rect  # visual rect
        if image_path:
            w = r.width * 0.5
            box = fitz.Rect(0, 0, w, w)
            box = box + (r.width / 2 - w / 2, r.height / 2 - w / 2,
                         r.width / 2 - w / 2, r.height / 2 - w / 2)
            box = box * page.derotation_matrix
            box.normalize()
            page.insert_image(box, filename=image_path, overlay=True,
                              keep_proportion=True, rotate=page.rotation)
            continue
        tl = fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
        center = fitz.Point(r.width / 2, r.height / 2) * page.derotation_matrix
        start = fitz.Point(center.x - tl / 2, center.y + fontsize / 3)
        mat = fitz.Matrix(angle + page.rotation)
        page.insert_text(start, text, fontsize=fontsize, fontname="helv", color=rgb,
                         fill_opacity=opacity, stroke_opacity=opacity,
                         morph=(center, mat), overlay=True)


def add_page_numbers(doc, pages, fmt="Page {n} of {total}", position="bottom-center",
                     fontsize=10, color="#000000", margin=24, start=1):
    rgb = hex_to_rgb(color)
    total = doc.page_count
    for i, p in enumerate(pages):
        page = doc[p]
        r = page.rect
        label = fmt.replace("{n}", str(start + i)).replace("{total}", str(total))
        tl = fitz.get_text_length(label, fontname="helv", fontsize=fontsize)
        vert, horiz = position.split("-")
        y = margin + fontsize if vert == "top" else r.height - margin
        x = {"left": margin, "center": (r.width - tl) / 2, "right": r.width - margin - tl}[horiz]
        pt = fitz.Point(x, y) * page.derotation_matrix
        page.insert_text(pt, label, fontsize=fontsize, fontname="helv", color=rgb,
                         rotate=page.rotation)


def add_header_footer(doc, pages, header="", footer="", fontsize=9, color="#444444", margin=20):
    rgb = hex_to_rgb(color)
    for p in pages:
        page = doc[p]
        r = page.rect
        for text, top in ((header, True), (footer, False)):
            if not text:
                continue
            tl = fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
            y = margin + fontsize if top else r.height - margin
            pt = fitz.Point((r.width - tl) / 2, y) * page.derotation_matrix
            page.insert_text(pt, text, fontsize=fontsize, fontname="helv", color=rgb,
                             rotate=page.rotation)


def find_span(page, point):
    """Return the text span (dict) under `point` (unrotated coords) or None."""
    d = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    best = None
    for b in d["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if fitz.Rect(s["bbox"]).contains(point) and s["text"].strip():
                    s = dict(s)
                    s["dir"] = line["dir"]
                    best = s
    return best


def page_lines(page):
    """All text lines of a page, each merged into one editable unit."""
    out = []
    d = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for b in d["blocks"]:
        for line in b.get("lines", []):
            spans = [s for s in line["spans"] if s["text"]]
            if not spans or not "".join(s["text"] for s in spans).strip():
                continue
            main = max(spans, key=lambda s: len(s["text"]))
            out.append({"text": "".join(s["text"] for s in spans),
                        "bbox": tuple(line["bbox"]), "size": main["size"],
                        "font": main["font"], "color": main["color"],
                        "flags": main["flags"], "origin": spans[0]["origin"],
                        "dir": line["dir"]})
    return out


def find_line(page, point, lines=None):
    """Return the text line under `point` (unrotated coords) or None."""
    for ln in (lines if lines is not None else page_lines(page)):
        if fitz.Rect(ln["bbox"]).contains(point):
            return ln
    return None


def _base_font(fontname: str, flags: int) -> str:
    n = (fontname or "").lower()
    bold = "bold" in n or "black" in n or "heavy" in n or flags & 16
    italic = "italic" in n or "oblique" in n or flags & 2
    serif = ("times" in n or "serif" in n and "sans" not in n or "georgia" in n
             or "garamond" in n or flags & 4)
    mono = "courier" in n or "mono" in n or flags & 8
    if mono:
        return {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"}[(int(bool(bold)), int(bool(italic)))]
    if serif:
        return {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"}[(int(bool(bold)), int(bool(italic)))]
    return {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"}[(int(bool(bold)), int(bool(italic)))]


def replace_text(page, span: dict, new_text: str, fontsize: Optional[float] = None,
                 color: Optional[str] = None, fontfile: Optional[str] = None):
    """Remove the original text in span['bbox'] and write `new_text` in its place."""
    rect = fitz.Rect(span["bbox"])
    page.add_redact_annot(rect, fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if not new_text:
        return
    size = fontsize or span["size"]
    rgb = hex_to_rgb(color) if color else tuple(((span["color"] >> s) & 255) / 255 for s in (16, 8, 0))
    kwargs = {}
    if fontfile and os.path.exists(fontfile):
        kwargs = {"fontname": "F_" + _base(fontfile)[:20].replace(" ", ""), "fontfile": fontfile}
    else:
        kwargs = {"fontname": _base_font(span.get("font", ""), span.get("flags", 0))}
    dx, dy = span.get("dir", (1, 0))
    import math
    rot = round(math.degrees(math.atan2(-dy, dx))) % 360
    rot = rot if rot in (0, 90, 180, 270) else 0
    page.insert_text(fitz.Point(span["origin"]), new_text, fontsize=size, color=rgb,
                     rotate=rot, **kwargs)


def search(doc, text, case_sensitive=False):
    """Return list of (page_index, Rect) hits (unrotated coords)."""
    hits = []
    if not text:
        return hits
    for i, page in enumerate(doc):
        rects = page.search_for(text)
        if case_sensitive:
            rects = [r for r in rects if text in page.get_textbox(r + (-1, -1, 1, 1))]
        hits.extend((i, r) for r in rects)
    return hits


def save_document(doc, path, user_pw="", owner_pw="", permissions=None, linearize=False):
    """Save with optional AES-256 encryption."""
    kwargs = dict(garbage=3, deflate=True)
    if user_pw or owner_pw:
        perm = permissions if permissions is not None else int(
            fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY |
            fitz.PDF_PERM_ANNOTATE)
        kwargs.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw,
                      owner_pw=owner_pw or user_pw, permissions=perm)
    else:
        kwargs.update(encryption=fitz.PDF_ENCRYPT_NONE)
    same = os.path.exists(path) and doc.name and os.path.abspath(doc.name) == os.path.abspath(path)
    if same:
        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=folder)
        os.close(fd)
        doc.save(tmp, **kwargs)
        return tmp  # caller closes doc and moves tmp over path
    doc.save(path, **kwargs)
    return path
