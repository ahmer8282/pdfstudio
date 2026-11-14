"""PDF Studio - dialogs."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QPointF, QStandardPaths
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPlainTextEdit, QPushButton, QRadioButton, QSlider, QSpinBox,
    QVBoxLayout, QWidget, QAbstractItemView, QListWidgetItem, QFontComboBox,
)

import core


class ColorButton(QPushButton):
    def __init__(self, color="#000000", parent=None):
        super().__init__(parent)
        self.setFixedWidth(46)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self._color = QColor(c).name()
        self.setStyleSheet(f"background:{self._color};border:1px solid #888;"
                           "border-radius:4px;min-height:20px")

    def color(self):
        return self._color

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Choose colour")
        if c.isValid():
            self.set_color(c.name())


def _buttons(dlg):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


# ---------------------------------------------------------------------------
class ExportDialog(QDialog):
    def __init__(self, parent, page_count, current_page):
        super().__init__(parent)
        self.setWindowTitle("Export / Convert")
        self.setMinimumWidth(440)
        lay = QFormLayout(self)
        self.fmt = QComboBox()
        for key, (label, _) in core.EXPORT_FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(list(core.EXPORT_FORMATS).index("docx"))
        lay.addRow("Format:", self.fmt)

        self.r_all = QRadioButton(f"All pages (1-{page_count})")
        self.r_cur = QRadioButton(f"Current page ({current_page + 1})")
        self.r_range = QRadioButton("Pages:")
        self.range = QLineEdit()
        self.range.setPlaceholderText("e.g. 1-3, 5, 8-")
        self.r_all.setChecked(True)
        rl = QVBoxLayout()
        rl.addWidget(self.r_all)
        rl.addWidget(self.r_cur)
        h = QHBoxLayout()
        h.addWidget(self.r_range)
        h.addWidget(self.range)
        rl.addLayout(h)
        self.range.textEdited.connect(lambda: self.r_range.setChecked(True))
        lay.addRow("Pages:", rl)

        self.dpi = QSpinBox()
        self.dpi.setRange(36, 1200)
        self.dpi.setValue(150)
        self.dpi.setSuffix(" dpi")
        lay.addRow("Image resolution:", self.dpi)
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(85)
        self.quality.setSuffix(" %")
        lay.addRow("JPG quality:", self.quality)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#666")
        lay.addRow(self.hint)
        lay.addRow(_buttons(self))
        self.fmt.currentIndexChanged.connect(self._update)
        self.page_count, self.current_page = page_count, current_page
        self._update()

    def _update(self):
        k = self.key()
        img = k in ("png", "jpg", "tiff", "pptx", "pdf_compressed")
        self.dpi.setEnabled(img)
        self.quality.setEnabled(k in ("jpg", "pdf_compressed"))
        hints = {
            "docx": "Editable Word file - keeps text, tables, images and layout.",
            "xlsx": "Every table found becomes a sheet. If no tables, text rows are exported.",
            "pptx": "Each page becomes a slide image; page text goes in speaker notes.",
            "pdf_compressed": "Downsamples images to the chosen dpi and cleans the file. 100-150 dpi is good for email.",
            "md": "Markdown with headings, lists and tables - good for AI tools and websites.",
            "images": "Saves every photo/logo embedded in the PDF in its original quality.",
            "csv": "One CSV file per table (opens in Excel).",
            "svg": "Vector page images - sharp at any size, editable in Illustrator/Inkscape.",
        }
        self.hint.setText(hints.get(k, ""))

    def key(self):
        return self.fmt.currentData()

    def pages(self):
        if self.r_cur.isChecked():
            return [self.current_page]
        if self.r_range.isChecked():
            return core.parse_ranges(self.range.text(), self.page_count)
        return list(range(self.page_count))


# ---------------------------------------------------------------------------
class TextDialog(QDialog):
    """Add or edit a block of text."""

    def __init__(self, parent, title, text="", size=12.0, color="#000000",
                 show_font=False, multiline=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text) if multiline else QLineEdit(text)
        if multiline:
            self.edit.setMinimumHeight(110)
        lay.addWidget(self.edit)
        f = QFormLayout()
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 200)
        self.size.setDecimals(1)
        self.size.setValue(size)
        self.color = ColorButton(color)
        row = QHBoxLayout()
        row.addWidget(self.size)
        row.addWidget(QLabel("Colour:"))
        row.addWidget(self.color)
        row.addStretch()
        f.addRow("Font size:", row)
        self.font = None
        if show_font:
            self.font = QComboBox()
            self.font.addItem("Match original (built-in)", "")
            for name, path in system_fonts():
                self.font.addItem(name, path)
            f.addRow("Font:", self.font)
        lay.addLayout(f)
        lay.addWidget(_buttons(self))
        self.edit.setFocus()

    def text(self):
        return self.edit.toPlainText() if isinstance(self.edit, QPlainTextEdit) else self.edit.text()

    def fontfile(self):
        return self.font.currentData() if self.font else ""


def system_fonts():
    """(name, path) of common TrueType fonts installed on Windows."""
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    wanted = [("Arial", "arial.ttf"), ("Arial Bold", "arialbd.ttf"),
              ("Calibri", "calibri.ttf"), ("Calibri Bold", "calibrib.ttf"),
              ("Times New Roman", "times.ttf"), ("Times New Roman Bold", "timesbd.ttf"),
              ("Segoe UI", "segoeui.ttf"), ("Segoe UI Bold", "segoeuib.ttf"),
              ("Tahoma (Arabic/Urdu)", "tahoma.ttf"), ("Verdana", "verdana.ttf"),
              ("Georgia", "georgia.ttf"), ("Courier New", "cour.ttf")]
    return [(n, os.path.join(folder, f)) for n, f in wanted
            if os.path.exists(os.path.join(folder, f))]


# ---------------------------------------------------------------------------
class WatermarkDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Add Watermark")
        self.page_count = page_count
        lay = QFormLayout(self)
        self.text = QLineEdit("CONFIDENTIAL")
        lay.addRow("Text:", self.text)
        img_row = QHBoxLayout()
        self.image = QLineEdit()
        self.image.setPlaceholderText("(optional) use an image/logo instead of text")
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        img_row.addWidget(self.image)
        img_row.addWidget(b)
        lay.addRow("Image:", img_row)
        self.size = QSpinBox()
        self.size.setRange(8, 300)
        self.size.setValue(72)
        lay.addRow("Font size:", self.size)
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setValue(45)
        self.angle.setSuffix(" °")
        lay.addRow("Angle:", self.angle)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(25)
        lay.addRow("Opacity:", self.opacity)
        self.color = ColorButton("#c0392b")
        lay.addRow("Colour:", self.color)
        self.range = QLineEdit()
        self.range.setPlaceholderText("all")
        lay.addRow("Pages:", self.range)
        lay.addRow(_buttons(self))

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Watermark image", "",
                                           "Images (*.png *.jpg *.jpeg *.bmp)")
        if f:
            self.image.setText(f)

    def pages(self):
        return core.parse_ranges(self.range.text(), self.page_count)


class PageNumberDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Page Numbers / Header & Footer")
        self.page_count = page_count
        lay = QFormLayout(self)
        self.enable_nums = QCheckBox("Add page numbers")
        self.enable_nums.setChecked(True)
        lay.addRow(self.enable_nums)
        self.fmt = QComboBox()
        self.fmt.setEditable(True)
        self.fmt.addItems(["Page {n} of {total}", "{n}", "- {n} -", "Page {n}", "{n} / {total}"])
        lay.addRow("Format:", self.fmt)
        self.pos = QComboBox()
        for v in ["bottom-center", "bottom-right", "bottom-left",
                  "top-center", "top-right", "top-left"]:
            self.pos.addItem(v.replace("-", " ").title(), v)
        lay.addRow("Position:", self.pos)
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(1)
        lay.addRow("Start at:", self.start)
        self.header = QLineEdit()
        self.header.setPlaceholderText("(optional) header text")
        lay.addRow("Header:", self.header)
        self.footer = QLineEdit()
        self.footer.setPlaceholderText("(optional) footer text")
        lay.addRow("Footer:", self.footer)
        self.size = QSpinBox()
        self.size.setRange(5, 48)
        self.size.setValue(10)
        lay.addRow("Font size:", self.size)
        self.color = ColorButton("#333333")
        lay.addRow("Colour:", self.color)
        self.range = QLineEdit()
        self.range.setPlaceholderText("all")
        lay.addRow("Pages:", self.range)
        lay.addRow(_buttons(self))

    def pages(self):
        return core.parse_ranges(self.range.text(), self.page_count)


class SplitDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Split PDF")
        lay = QFormLayout(self)
        self.m_single = QRadioButton("One file per page")
        self.m_every = QRadioButton("Every N pages:")
        self.m_ranges = QRadioButton("Custom ranges:")
        self.m_every.setChecked(True)
        self.n = QSpinBox()
        self.n.setRange(1, max(1, page_count))
        self.n.setValue(min(2, page_count))
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("1-3; 4-10; 11-")
        lay.addRow(self.m_single)
        h = QHBoxLayout()
        h.addWidget(self.m_every)
        h.addWidget(self.n)
        lay.addRow(h)
        h2 = QHBoxLayout()
        h2.addWidget(self.m_ranges)
        h2.addWidget(self.ranges)
        lay.addRow(h2)
        lay.addRow(QLabel(f"Document has {page_count} pages. Separate ranges with ';'"))
        lay.addRow(_buttons(self))

    def mode(self):
        if self.m_single.isChecked():
            return "single", ""
        if self.m_every.isChecked():
            return "every", str(self.n.value())
        return "ranges", self.ranges.text()


class ProtectDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Password Protect (AES-256)")
        lay = QFormLayout(self)
        self.user = QLineEdit()
        self.user.setEchoMode(QLineEdit.Password)
        self.user2 = QLineEdit()
        self.user2.setEchoMode(QLineEdit.Password)
        self.owner = QLineEdit()
        self.owner.setEchoMode(QLineEdit.Password)
        self.owner.setPlaceholderText("(optional) needed to change permissions")
        lay.addRow("Open password:", self.user)
        lay.addRow("Confirm:", self.user2)
        lay.addRow("Owner password:", self.owner)
        self.p_print = QCheckBox("Allow printing")
        self.p_copy = QCheckBox("Allow copying text")
        self.p_edit = QCheckBox("Allow editing")
        self.p_annot = QCheckBox("Allow comments / form filling")
        for c in (self.p_print, self.p_copy, self.p_annot):
            c.setChecked(True)
            lay.addRow(c)
        lay.addRow(self.p_edit)
        self.msg = QLabel()
        self.msg.setStyleSheet("color:#c0392b")
        lay.addRow(self.msg)
        bb = _buttons(self)
        bb.accepted.disconnect()
        bb.accepted.connect(self._check)
        lay.addRow(bb)

    def _check(self):
        if not self.user.text() and not self.owner.text():
            self.msg.setText("Enter at least one password.")
        elif self.user.text() != self.user2.text():
            self.msg.setText("Passwords do not match.")
        else:
            self.accept()

    def permissions(self):
        import pymupdf as fitz
        p = fitz.PDF_PERM_ACCESSIBILITY
        if self.p_print.isChecked():
            p |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
        if self.p_copy.isChecked():
            p |= fitz.PDF_PERM_COPY
        if self.p_edit.isChecked():
            p |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ASSEMBLE
        if self.p_annot.isChecked():
            p |= fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM
        return int(p)


class MetadataDialog(QDialog):
    FIELDS = ["title", "author", "subject", "keywords", "creator", "producer"]

    def __init__(self, parent, meta, info):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.setMinimumWidth(460)
        lay = QFormLayout(self)
        self.edits = {}
        for f in self.FIELDS:
            e = QLineEdit(meta.get(f) or "")
            self.edits[f] = e
            lay.addRow(f.title() + ":", e)
        for k, v in info:
            l = QLabel(str(v))
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addRow(k + ":", l)
        lay.addRow(_buttons(self))

    def metadata(self, old):
        m = dict(old)
        for f, e in self.edits.items():
            m[f] = e.text()
        return m


class FileListDialog(QDialog):
    """Pick and order several files (merge / images to PDF)."""

    def __init__(self, parent, title, filt, initial=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 400)
        self.filt = filt
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Drag to reorder. Files are combined top to bottom."))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in (("Add files...", self._add), ("Remove", self._remove),
                         ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_buttons(self))
        for f in initial or []:
            self._item(f)

    def _item(self, f):
        it = QListWidgetItem(os.path.basename(f))
        it.setToolTip(f)
        it.setData(Qt.UserRole, f)
        self.list.addItem(it)

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add files", "", self.filt)
        for f in files:
            self._item(f)

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _move(self, d):
        r = self.list.currentRow()
        if r < 0 or not (0 <= r + d < self.list.count()):
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def files(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]


# ---------------------------------------------------------------------------
class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 200)
        self.paths = []
        self.pen_color = QColor("#0b2e8a")
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor("#cccccc"), 1, Qt.DashLine))
        y = int(self.height() * 0.75)
        p.drawLine(20, y, self.width() - 20, y)
        p.setPen(QPen(self.pen_color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)

    def mousePressEvent(self, e):
        path = QPainterPath(e.position())
        self.paths.append(path)

    def mouseMoveEvent(self, e):
        if self.paths and e.buttons() & Qt.LeftButton:
            self.paths[-1].lineTo(e.position())
            self.update()

    def clear(self):
        self.paths = []
        self.update()

    def image(self):
        if not self.paths:
            return None
        bounds = self.paths[0].boundingRect()
        for p in self.paths[1:]:
            bounds = bounds.united(p.boundingRect())
        bounds.adjust(-6, -6, 6, 6)
        scale = 3
        img = QImage(int(bounds.width() * scale), int(bounds.height() * scale),
                     QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(scale, scale)
        p.translate(-bounds.topLeft())
        p.setPen(QPen(self.pen_color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)
        p.end()
        return img


def signature_path():
    folder = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "signature.png")


class SignatureDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Signature")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Draw your signature with the mouse (or load an image). "
                             "Then drag a box on the page where it should go."))
        self.pad = SignaturePad()
        lay.addWidget(self.pad)
        row = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.pad.clear)
        col = ColorButton("#0b2e8a")
        col.clicked.connect(lambda: (setattr(self.pad, "pen_color", QColor(col.color())), self.pad.update()))
        load = QPushButton("Load image...")
        load.clicked.connect(self._load)
        self.use_saved = QPushButton("Use saved signature")
        self.use_saved.setEnabled(os.path.exists(signature_path()))
        self.use_saved.clicked.connect(self._saved)
        for w in (clear, QLabel("Ink:"), col, load, self.use_saved):
            row.addWidget(w)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_buttons(self))
        self.result_path = None

    def _load(self):
        f, _ = QFileDialog.getOpenFileName(self, "Signature image", "",
                                           "Images (*.png *.jpg *.jpeg *.bmp)")
        if f:
            self.result_path = f
            QDialog.accept(self)

    def _saved(self):
        self.result_path = signature_path()
        QDialog.accept(self)

    def accept(self):
        img = self.pad.image()
        if img is None:
            return
        path = signature_path()
        img.save(path, "PNG")
        self.result_path = path
        super().accept()
