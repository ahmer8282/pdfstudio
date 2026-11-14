"""PDF Studio - view, edit and convert PDF files on Windows."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback

import pymupdf as fitz
from PySide6.QtCore import (QPointF, QRectF, QSettings, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QImage,
                           QKeySequence, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDockWidget, QFileDialog,
    QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressDialog, QSlider, QSpinBox, QToolBar,
    QToolButton, QWidget, QHBoxLayout, QStyle, QSizePolicy,
)

import core
import dialogs

APP_NAME = "PDF Studio"
VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))

OPEN_FILTER = ("All supported (*.pdf *.xps *.oxps *.epub *.cbz *.fb2 *.mobi *.png *.jpg *.jpeg "
               "*.bmp *.gif *.tif *.tiff *.webp *.svg *.txt);;PDF (*.pdf);;Images (*.png *.jpg "
               "*.jpeg *.bmp *.gif *.tif *.tiff *.webp);;All files (*)")
INSERT_FILTER = "PDF or images (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.xps *.epub)"

# tool id: (label, glyph, tooltip)
TOOLS = {
    "select": ("Select", "➤", "Select, move and delete comments. Right-click a comment for options. (Esc)"),
    "edittext": ("Edit Text", "✎", "Click any existing line of text to change it"),
    "text": ("Add Text", "T", "Click where the new text box should go"),
    "highlight": ("Highlight", "▌", "Drag across text to highlight it"),
    "underline": ("Underline", "U̲", "Drag across text to underline it"),
    "strike": ("Strike", "S̶", "Drag across text to strike it out"),
    "rect": ("Box", "▭", "Drag to draw a rectangle"),
    "ellipse": ("Circle", "◯", "Drag to draw an ellipse"),
    "line": ("Line", "╱", "Drag to draw a line"),
    "arrow": ("Arrow", "➔", "Drag to draw an arrow"),
    "ink": ("Draw", "✍", "Freehand drawing / pen"),
    "note": ("Note", "🗨", "Click to add a sticky-note comment"),
    "image": ("Image", "🖼", "Drag a box (or click) to insert a picture"),
    "sign": ("Sign", "✒", "Draw or load your signature, then drag where to place it"),
    "whiteout": ("Whiteout", "⬜", "Drag to cover an area with a white box"),
    "redact": ("Redact", "⬛", "Drag to PERMANENTLY remove text/images underneath"),
}


def glyph_icon(ch: str, color="#2d3748", size=64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont("Segoe UI Symbol")
    f.setPixelSize(int(size * 0.62))
    f.setBold(ch in ("T",))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(pm.rect(), Qt.AlignCenter, ch)
    p.end()
    return QIcon(pm)


def app_icon() -> QIcon:
    path = os.path.join(HERE, "icon.ico")
    if os.path.exists(path):
        return QIcon(path)
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#c0392b"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 8, 112, 112, 22, 22)
    p.setPen(Qt.white)
    f = QFont("Segoe UI", 36, QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "PDF")
    p.end()
    return QIcon(pm)


def pix_to_qimage(pix: fitz.Pixmap) -> QImage:
    fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


def rgb_tuple(hexcolor: str):
    return core.hex_to_rgb(hexcolor)


# =========================================================================== background tasks
def _child_task(fn_name, args, kwargs, q):
    """Runs in a separate process: MuPDF is not thread-safe, and a crash in a
    converter must never take the editor (and unsaved work) down with it."""
    try:
        import core as _core
        import pymupdf as _fitz
        args = [_fitz.open(a[1]) if isinstance(a, tuple) and len(a) == 2 and a[0] == "__doc__" else a
                for a in args]
        res = getattr(_core, fn_name)(*args, progress=lambda d, t: q.put(("p", d, t)), **kwargs)
        q.put(("ok", res))
    except Exception as e:  # noqa
        q.put(("err", f"{type(e).__name__}: {e}\n\n" + traceback.format_exc()[-1500:]))


# =========================================================================== page view
class PageView(QGraphicsView):
    """Shows one page and turns mouse gestures into edits."""
    pageChangeRequest = Signal(int)  # +1 / -1
    zoomRequest = Signal(float)      # factor

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setBackgroundBrush(QColor("#5b6270"))
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.page_item = None
        self.zoom = 1.0
        self.page = None
        self.temp = None          # rubber-band item
        self.overlays = []        # search hits / selection / hover
        self.hover_item = None
        self.drag_start = None
        self.ink_points = []
        self.sel_annot_xref = None
        self.moving = False
        self.lines_cache = None
        self.wheel_accum = 0

    # ---- coordinate helpers
    def to_pdf(self, scene_pt: QPointF) -> fitz.Point:
        p = fitz.Point(scene_pt.x() / self.zoom, scene_pt.y() / self.zoom)
        return p * self.page.derotation_matrix

    def to_scene_rect(self, r: fitz.Rect) -> QRectF:
        v = fitz.Rect(r) * self.page.rotation_matrix
        v.normalize()
        return QRectF(v.x0 * self.zoom, v.y0 * self.zoom, v.width * self.zoom, v.height * self.zoom)

    def pdf_rect(self, a: QPointF, b: QPointF) -> fitz.Rect:
        r = fitz.Rect(self.to_pdf(a), self.to_pdf(b))
        r.normalize()
        return r

    # ---- rendering
    def show_page(self, page: fitz.Page, zoom: float, keep_scroll=False):
        hs, vs = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        self.page, self.zoom = page, zoom
        self.lines_cache = None
        self.scene_.clear()
        self.overlays, self.temp, self.hover_item = [], None, None
        dpr = self.devicePixelRatioF()
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom * dpr, zoom * dpr), alpha=False, annots=True)
        qpm = QPixmap.fromImage(pix_to_qimage(pix))
        qpm.setDevicePixelRatio(dpr)
        self.page_item = QGraphicsPixmapItem(qpm)
        self.page_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene_.addItem(self.page_item)
        w, h = pix.width / dpr, pix.height / dpr
        shadow = self.scene_.addRect(QRectF(4, 4, w, h), QPen(Qt.NoPen), QBrush(QColor(0, 0, 0, 60)))
        shadow.setZValue(-1)
        self.scene_.setSceneRect(QRectF(-20, -20, w + 40, h + 40))
        self.draw_overlays()
        if keep_scroll:
            self.horizontalScrollBar().setValue(hs)
            self.verticalScrollBar().setValue(vs)

    def clear_page(self):
        self.scene_.clear()
        self.page = None
        self.page_item = None

    def draw_overlays(self):
        for it in self.overlays:
            self.scene_.removeItem(it)
        self.overlays = []
        if not self.page:
            return
        pno = self.page.number
        for i, (hp, r) in enumerate(self.win.search_hits):
            if hp != pno:
                continue
            cur = i == self.win.search_index
            it = self.scene_.addRect(self.to_scene_rect(r), QPen(Qt.NoPen),
                                     QBrush(QColor(255, 140, 0, 110) if cur else QColor(255, 230, 0, 90)))
            self.overlays.append(it)
        if self.sel_annot_xref:
            a = self.find_annot_by_xref(self.sel_annot_xref)
            if a:
                pen = QPen(QColor("#1e88e5"), 1.5, Qt.DashLine)
                it = self.scene_.addRect(self.to_scene_rect(a.rect).adjusted(-3, -3, 3, 3), pen)
                self.overlays.append(it)

    def find_annot_by_xref(self, xref):
        for a in self.page.annots():
            if a.xref == xref:
                return a
        return None

    def annot_at(self, pt: fitz.Point):
        hit = None
        for a in self.page.annots():
            r = a.rect + (-3, -3, 3, 3)
            if r.contains(pt):
                hit = a  # last = top-most
        return hit

    # ---- mouse
    def tool(self):
        return self.win.current_tool

    def mousePressEvent(self, e):
        if not self.page or e.button() == Qt.MiddleButton:
            return super().mousePressEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        pt = self.to_pdf(sp)
        t = self.tool()
        if e.button() == Qt.RightButton:
            a = self.annot_at(pt)
            if a:
                self.sel_annot_xref = a.xref
                self.draw_overlays()
                self.win.annot_menu(a, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if t == "select":
            a = self.annot_at(pt)
            self.sel_annot_xref = a.xref if a else None
            self.draw_overlays()
            if a:
                self.drag_start = sp
                self.moving = True
            else:
                self.setDragMode(QGraphicsView.ScrollHandDrag)
                super().mousePressEvent(e)
            return
        if t == "edittext":
            ln = core.find_line(self.page, pt, self.get_lines())
            if ln:
                self.win.edit_text_line(ln)
            return
        if t in ("text", "note"):
            self.win.add_point_item(t, pt)
            return
        self.drag_start = sp
        if t == "ink":
            self.ink_points = [sp]
            self.temp = QGraphicsPathItem(QPainterPath(sp))
            self.temp.setPen(QPen(QColor(self.win.color), max(1, self.win.width * self.zoom),
                                  Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            self.temp = QGraphicsRectItem(QRectF(sp, sp))
            color = QColor(self.win.color)
            if t in ("whiteout",):
                color = QColor("#888888")
            if t == "redact":
                color = QColor("#000000")
            self.temp.setPen(QPen(color, 1.5, Qt.DashLine))
            fill = QColor(color)
            fill.setAlpha(40)
            self.temp.setBrush(QBrush(fill))
        self.scene_.addItem(self.temp)

    def mouseMoveEvent(self, e):
        if not self.page:
            return super().mouseMoveEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        t = self.tool()
        if self.moving and self.drag_start is not None:
            d = sp - self.drag_start
            for it in self.overlays[-1:]:
                it.setPos(d)
            return
        if self.temp is not None and self.drag_start is not None:
            if t == "ink":
                self.ink_points.append(sp)
                path = self.temp.path()
                path.lineTo(sp)
                self.temp.setPath(path)
            elif t in ("line", "arrow"):
                self.scene_.removeItem(self.temp)
                path = QPainterPath(self.drag_start)
                path.lineTo(sp)
                self.temp = QGraphicsPathItem(path)
                self.temp.setPen(QPen(QColor(self.win.color), max(1, self.win.width * self.zoom)))
                self.scene_.addItem(self.temp)
            else:
                self.temp.setRect(QRectF(self.drag_start, sp).normalized())
            return
        if t == "edittext":
            self.hover_line(self.to_pdf(sp))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if not self.page:
            return super().mouseReleaseEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        t = self.tool()
        if self.moving:
            self.moving = False
            start, self.drag_start = self.drag_start, None
            if start is not None and (sp - start).manhattanLength() > 3:
                a = self.find_annot_by_xref(self.sel_annot_xref)
                if a:
                    dv = self.to_pdf(sp) - self.to_pdf(start)
                    self.win.move_annot(a, dv)
            else:
                self.draw_overlays()
            return
        if self.dragMode() == QGraphicsView.ScrollHandDrag:
            super().mouseReleaseEvent(e)
            self.setDragMode(QGraphicsView.NoDrag)
            return
        if self.drag_start is None:
            return super().mouseReleaseEvent(e)
        start, self.drag_start = self.drag_start, None
        if self.temp is not None:
            self.scene_.removeItem(self.temp)
            self.temp = None
        if t == "ink":
            pts = [self.to_pdf(p) for p in self.ink_points]
            self.ink_points = []
            if len(pts) > 1:
                self.win.add_ink([[(p.x, p.y) for p in pts]])
            return
        a, b = self.to_pdf(start), self.to_pdf(sp)
        small = (sp - start).manhattanLength() < 6
        if t in ("line", "arrow"):
            if not small:
                self.win.add_line(a, b, arrow=(t == "arrow"))
            return
        rect = fitz.Rect(a, b)
        rect.normalize()
        self.win.add_rect_item(t, rect, small)

    def hover_line(self, pt):
        ln = core.find_line(self.page, pt, self.get_lines())
        if self.hover_item:
            self.scene_.removeItem(self.hover_item)
            self.hover_item = None
        if ln:
            self.hover_item = self.scene_.addRect(self.to_scene_rect(fitz.Rect(ln["bbox"])),
                                                  QPen(QColor("#1e88e5"), 1.2),
                                                  QBrush(QColor(30, 136, 229, 30)))
            self.setCursor(Qt.IBeamCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def get_lines(self):
        if self.lines_cache is None:
            self.lines_cache = core.page_lines(self.page)
        return self.lines_cache

    def mouseDoubleClickEvent(self, e):
        if not self.page:
            return
        pt = self.to_pdf(self.mapToScene(e.position().toPoint()))
        a = self.annot_at(pt)
        if a and self.tool() == "select":
            self.win.edit_annot(a)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoomRequest.emit(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            return
        vs = self.verticalScrollBar()
        dy = e.angleDelta().y()
        at_end = vs.value() >= vs.maximum() and dy < 0
        at_top = vs.value() <= vs.minimum() and dy > 0
        if at_end or at_top:
            self.wheel_accum += dy
            if abs(self.wheel_accum) >= 240 or vs.maximum() == 0:
                self.wheel_accum = 0
                self.pageChangeRequest.emit(1 if at_end else -1)
            return
        self.wheel_accum = 0
        super().wheelEvent(e)

    def update_cursor(self):
        t = self.tool()
        self.setCursor({"select": Qt.ArrowCursor, "edittext": Qt.IBeamCursor,
                        "text": Qt.IBeamCursor}.get(t, Qt.CrossCursor))


# =========================================================================== thumbnails
class ThumbList(QListWidget):
    reordered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setIconSize(QSize(120, 160))
        self.setGridSize(QSize(140, 172))
        self.setUniformItemSizes(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setStyleSheet("QListWidget{background:#eef0f4;border:none}"
                           "QListWidget::item{color:#333;border-radius:6px;padding:4px}"
                           "QListWidget::item:selected{background:#cfe3ff;color:#000}")

    def dropEvent(self, e):
        super().dropEvent(e)
        order = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        if order != sorted(order):
            QTimer.singleShot(0, lambda: self.reordered.emit(order))

    def selected_pages(self):
        return sorted(self.row(i) for i in self.selectedItems())


# =========================================================================== main window
class MainWindow(QMainWindow):
    windows = []

    def __init__(self, path=None):
        super().__init__()
        MainWindow.windows.append(self)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(app_icon())
        self.settings = QSettings("PDFStudio", "PDFStudio")
        self.doc: fitz.Document | None = None
        self.path = None
        self.password = None
        self.page_no = 0
        self.zoom = 1.0
        self.zoom_mode = "width"  # width | page | custom
        self.modified = False
        self.undo_stack, self.redo_stack = [], []
        self.search_hits, self.search_index = [], -1
        self.current_tool = "select"
        self.color = self.settings.value("color", "#e53935")
        self.width = float(self.settings.value("width", 2))
        self.fontsize = float(self.settings.value("fontsize", 12))
        self.opacity = 1.0
        self.signature_file = None
        self.encrypt = None  # (user, owner, perms) to apply on next save
        self._threads = []

        self.view = PageView(self)
        self.view.pageChangeRequest.connect(self.step_page)
        self.view.zoomRequest.connect(lambda f: self.set_zoom(self.zoom * f))
        self.setCentralWidget(self.view)
        self.thumbs = ThumbList()
        self.thumbs.currentRowChanged.connect(self._thumb_clicked)
        self.thumbs.reordered.connect(self.reorder_pages)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        dock = QDockWidget("Pages", self)
        dock.setObjectName("pages")
        dock.setWidget(self.thumbs)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.thumb_dock = dock
        self.resizeDocks([dock], [190], Qt.Horizontal)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.timeout.connect(self._render_thumb_batch)
        self._thumb_queue = []

        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)
        self.setAcceptDrops(True)
        self.resize(1300, 860)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.update_ui()
        if path:
            QTimer.singleShot(0, lambda: self.open_file(path))

    # ------------------------------------------------------------------ actions
    def act(self, text, slot, shortcut=None, tip=None, icon=None):
        a = QAction(text, self)
        a.triggered.connect(lambda checked=False: slot())
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        if icon is not None:
            a.setIcon(icon if isinstance(icon, QIcon) else glyph_icon(icon))
        return a

    def _build_actions(self):
        sp = self.style().standardIcon
        A = self.act
        self.a_open = A("&Open...", self.open_dialog, QKeySequence.Open, "Open a PDF, image, XPS or EPUB", sp(QStyle.SP_DialogOpenButton))
        self.a_new = A("&New blank PDF", self.new_blank, "Ctrl+N")
        self.a_newwin = A("New &window", lambda: MainWindow().show(), "Ctrl+Shift+N")
        self.a_save = A("&Save", self.save, QKeySequence.Save, "Save", sp(QStyle.SP_DialogSaveButton))
        self.a_saveas = A("Save &As...", self.save_as, "Ctrl+Shift+S")
        self.a_export = A("&Export / Convert...", self.export_dialog, "Ctrl+E", "Export to Word, Excel, PowerPoint, images, text...", "⇪")
        self.a_print = A("&Print...", self.print_doc, QKeySequence.Print, "Print", "⎙")
        self.a_props = A("Document &Properties...", self.properties, "Ctrl+D")
        self.a_close = A("&Close document", self.close_doc, "Ctrl+W")
        self.a_exit = A("E&xit", self.close, "Alt+F4")
        self.a_undo = A("&Undo", self.undo, QKeySequence.Undo, "Undo", "↶")
        self.a_redo = A("&Redo", self.redo, "Ctrl+Y", "Redo", "↷")
        self.a_find = A("&Find...", lambda: (self.search_box.setFocus(), self.search_box.selectAll()), QKeySequence.Find)
        self.a_copytext = A("Copy page &text", self.copy_page_text, "Ctrl+Shift+C")
        self.a_zin = A("Zoom &In", lambda: self.set_zoom(self.zoom * 1.2), QKeySequence.ZoomIn, "Zoom in", "+")
        self.a_zout = A("Zoom &Out", lambda: self.set_zoom(self.zoom / 1.2), QKeySequence.ZoomOut, "Zoom out", "−")
        self.a_fitw = A("Fit &Width", lambda: self.set_zoom_mode("width"), "Ctrl+1", "Fit width", "↔")
        self.a_fitp = A("Fit &Page", lambda: self.set_zoom_mode("page"), "Ctrl+2", "Fit page", "⤢")
        self.a_actual = A("&Actual size", lambda: self.set_zoom(1.0), "Ctrl+0")
        self.a_prev = A("Previous page", lambda: self.step_page(-1), "PgUp", "Previous page", "◀")
        self.a_next = A("Next page", lambda: self.step_page(1), "PgDown", "Next page", "▶")
        self.a_first = A("First page", lambda: self.go_page(0), "Home")
        self.a_last = A("Last page", lambda: self.go_page(self.doc.page_count - 1 if self.doc else 0), "End")
        self.a_thumbs = self.thumb_dock.toggleViewAction()
        self.a_thumbs.setText("Page &thumbnails")
        self.a_thumbs.setShortcut("F4")
        # page
        self.a_rotl = A("Rotate Left", lambda: self.rotate_pages(-90), "Ctrl+Shift+-", "Rotate selected pages left", "⟲")
        self.a_rotr = A("Rotate Right", lambda: self.rotate_pages(90), "Ctrl+Shift+=", "Rotate selected pages right", "⟳")
        self.a_delpage = A("Delete Page(s)", self.delete_pages, "Ctrl+Del", "Delete selected pages", "🗑")
        self.a_blank = A("Insert Blank Page", self.insert_blank, None, "Insert a blank page after the current page", "＋")
        self.a_insfile = A("Insert Pages from File...", self.insert_file, None, "Insert pages from another PDF or images")
        self.a_dup = A("Duplicate Page", self.duplicate_page)
        self.a_extract = A("Extract Selected Pages...", self.extract_pages)
        self.a_mvup = A("Move Page Up", lambda: self.move_page(-1), "Ctrl+Up")
        self.a_mvdn = A("Move Page Down", lambda: self.move_page(1), "Ctrl+Down")
        self.a_crop = A("Crop Page to Selection...", self.crop_hint)
        self.a_reverse = A("Reverse Page Order", self.reverse_pages)
        # document tools
        self.a_merge = A("&Merge / Combine Files...", self.merge_files, None, "Combine PDFs and images into one PDF", "⧉")
        self.a_split = A("&Split PDF...", self.split_doc)
        self.a_wm = A("Add &Watermark...", self.watermark)
        self.a_nums = A("Page &Numbers / Header / Footer...", self.page_numbers)
        self.a_protect = A("&Password Protect...", self.protect, None, "Encrypt with a password", "🔒")
        self.a_unprotect = A("&Remove Password", self.unprotect)
        self.a_compress = A("&Compress / Reduce Size...", self.compress)
        self.a_flatten = A("&Flatten Comments && Forms", self.flatten)
        self.a_img2pdf = A("&Images to PDF...", self.images_to_pdf)
        self.a_remannots = A("Remove &All Comments", self.remove_all_annots)
        self.a_about = A("&About", self.about)
        self.a_shortcuts = A("&Keyboard shortcuts", self.shortcuts, "F1")

        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, (label, g, tip) in TOOLS.items():
            a = QAction(glyph_icon(g, "#000" if key in ("redact",) else "#2d3748"), label, self)
            a.setCheckable(True)
            a.setToolTip(f"{label} - {tip}")
            a.setStatusTip(tip)
            a.triggered.connect(lambda checked=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        esc = QAction(self)
        esc.setShortcut("Esc")
        esc.triggered.connect(lambda: self.set_tool("select"))
        self.addAction(esc)
        dele = QAction(self)
        dele.setShortcut(QKeySequence.Delete)
        dele.triggered.connect(self.delete_selected_annot)
        self.addAction(dele)

        self.doc_actions = [self.a_save, self.a_saveas, self.a_export, self.a_print, self.a_props,
                            self.a_close, self.a_find, self.a_copytext, self.a_zin, self.a_zout,
                            self.a_fitw, self.a_fitp, self.a_actual, self.a_prev, self.a_next,
                            self.a_first, self.a_last, self.a_rotl, self.a_rotr, self.a_delpage,
                            self.a_blank, self.a_insfile, self.a_dup, self.a_extract, self.a_mvup,
                            self.a_mvdn, self.a_crop, self.a_reverse, self.a_split, self.a_wm,
                            self.a_nums, self.a_protect, self.a_unprotect, self.a_compress,
                            self.a_flatten, self.a_remannots] + list(self.tool_actions.values())

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.a_open, self.a_new, self.a_newwin])
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addActions([self.a_save, self.a_saveas, self.a_export])
        m.addSeparator()
        m.addActions([self.a_print, self.a_props])
        m.addSeparator()
        m.addActions([self.a_close, self.a_exit])
        m = mb.addMenu("&Edit")
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addActions([self.a_find, self.a_copytext])
        m = mb.addMenu("&View")
        m.addActions([self.a_zin, self.a_zout, self.a_fitw, self.a_fitp, self.a_actual])
        m.addSeparator()
        m.addActions([self.a_first, self.a_prev, self.a_next, self.a_last])
        m.addSeparator()
        m.addAction(self.a_thumbs)
        m = mb.addMenu("&Tools")
        m.addActions(list(self.tool_actions.values()))
        m = mb.addMenu("&Pages")
        m.addActions([self.a_rotl, self.a_rotr, self.a_delpage])
        m.addSeparator()
        m.addActions([self.a_blank, self.a_insfile, self.a_dup, self.a_extract])
        m.addSeparator()
        m.addActions([self.a_mvup, self.a_mvdn, self.a_reverse, self.a_crop])
        m = mb.addMenu("&Document")
        m.addActions([self.a_merge, self.a_split, self.a_img2pdf])
        m.addSeparator()
        m.addActions([self.a_wm, self.a_nums])
        m.addSeparator()
        m.addActions([self.a_protect, self.a_unprotect, self.a_compress])
        m.addSeparator()
        m.addActions([self.a_flatten, self.a_remannots])
        m = mb.addMenu("&Help")
        m.addActions([self.a_shortcuts, self.a_about])

    def _build_toolbars(self):
        tb = QToolBar("Main", self)
        tb.setObjectName("main")
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addActions([self.a_open, self.a_save, self.a_export, self.a_print])
        tb.addSeparator()
        tb.addActions([self.a_undo, self.a_redo])
        tb.addSeparator()
        tb.addAction(self.a_prev)
        self.page_box = QSpinBox()
        self.page_box.setMinimum(1)
        self.page_box.setFixedWidth(64)
        self.page_box.setKeyboardTracking(False)
        self.page_box.valueChanged.connect(lambda v: self.go_page(v - 1))
        tb.addWidget(self.page_box)
        self.page_total = QLabel(" / 0 ")
        tb.addWidget(self.page_total)
        tb.addAction(self.a_next)
        tb.addSeparator()
        tb.addActions([self.a_zout])
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setFixedWidth(96)
        self.zoom_box.addItems(["Fit width", "Fit page", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"])
        self.zoom_box.activated.connect(self._zoom_box_changed)
        self.zoom_box.lineEdit().returnPressed.connect(self._zoom_box_changed)
        tb.addWidget(self.zoom_box)
        tb.addActions([self.a_zin, self.a_rotl, self.a_rotr])
        tb.addSeparator()
        tb.addActions([self.a_merge, self.a_protect])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search text...  (Enter)")
        self.search_box.setFixedWidth(220)
        self.search_box.returnPressed.connect(self.do_search)
        self.search_box.setClearButtonEnabled(True)
        tb.addWidget(self.search_box)
        self.search_info = QLabel("")
        tb.addWidget(self.search_info)
        a_sp = self.act("Previous match", lambda: self.next_hit(-1), "Shift+F3", "Previous match", "▲")
        a_sn = self.act("Next match", lambda: self.next_hit(1), "F3", "Next match", "▼")
        tb.addActions([a_sp, a_sn])

        tt = QToolBar("Edit tools", self)
        tt.setObjectName("tools")
        tt.setIconSize(QSize(22, 22))
        tt.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tt.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(tt)
        for k, a in self.tool_actions.items():
            tt.addAction(a)
            if k in ("edittext", "strike", "ink", "sign"):
                tt.addSeparator()
        tt.addSeparator()
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 0, 6, 0)
        h.addWidget(QLabel("Colour"))
        self.color_btn = dialogs.ColorButton(self.color)
        self.color_btn.clicked.connect(lambda: QTimer.singleShot(0, self._color_changed))
        h.addWidget(self.color_btn)
        h.addWidget(QLabel("  Size"))
        self.size_box = QSpinBox()
        self.size_box.setRange(4, 144)
        self.size_box.setValue(int(self.fontsize))
        self.size_box.setToolTip("Font size for new text")
        self.size_box.valueChanged.connect(lambda v: setattr(self, "fontsize", float(v)))
        h.addWidget(self.size_box)
        h.addWidget(QLabel("  Line"))
        self.width_box = QSpinBox()
        self.width_box.setRange(1, 30)
        self.width_box.setValue(int(self.width))
        self.width_box.setToolTip("Line thickness")
        self.width_box.valueChanged.connect(lambda v: setattr(self, "width", float(v)))
        h.addWidget(self.width_box)
        h.addWidget(QLabel("  Opacity"))
        self.opacity_sl = QSlider(Qt.Horizontal)
        self.opacity_sl.setRange(10, 100)
        self.opacity_sl.setValue(100)
        self.opacity_sl.setFixedWidth(80)
        self.opacity_sl.valueChanged.connect(lambda v: setattr(self, "opacity", v / 100))
        h.addWidget(self.opacity_sl)
        tt.addWidget(w)

    def _color_changed(self):
        self.color = self.color_btn.color()

    # ------------------------------------------------------------------ helpers
    def msg(self, text, ms=5000):
        self.statusBar().showMessage(text, ms)

    def error(self, text):
        QMessageBox.critical(self, APP_NAME, text)

    def need_doc(self):
        return self.doc is not None

    def update_ui(self):
        has = self.doc is not None
        for a in self.doc_actions:
            a.setEnabled(has)
        self.a_undo.setEnabled(bool(self.undo_stack))
        self.a_redo.setEnabled(bool(self.redo_stack))
        name = os.path.basename(self.path) if self.path else ("Untitled.pdf" if has else "")
        title = f"{'*' if self.modified else ''}{name} - {APP_NAME}" if has else APP_NAME
        self.setWindowTitle(title)
        if has:
            self.page_box.blockSignals(True)
            self.page_box.setMaximum(self.doc.page_count)
            self.page_box.setValue(self.page_no + 1)
            self.page_box.blockSignals(False)
            self.page_total.setText(f" / {self.doc.page_count} ")
            pg = self.doc[self.page_no]
            r = pg.rect
            self.status.setText(f"Page {self.page_no + 1} of {self.doc.page_count}   |   "
                                f"{r.width / 72 * 25.4:.0f} × {r.height / 72 * 25.4:.0f} mm   |   "
                                f"Zoom {self.zoom * 100:.0f}%   |   Tool: {TOOLS[self.current_tool][0]}")
            if self.zoom_mode == "custom":
                self.zoom_box.setEditText(f"{self.zoom * 100:.0f}%")
        else:
            self.page_total.setText(" / 0 ")
            self.status.setText("Open a PDF (Ctrl+O) or drag a file here.")

    def checkpoint(self):
        """Save state for undo before a change."""
        self.undo_stack.append(self.doc.tobytes(garbage=0))
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def changed(self, rebuild_thumbs=False, page_only=True):
        self.modified = True
        if rebuild_thumbs:
            self.build_thumbs()
        else:
            self.refresh_thumb(self.page_no)
        self.render(keep_scroll=True)
        self.update_ui()

    def _restore_bytes(self, data):
        self.doc.close()
        self.doc = fitz.open("pdf", data)
        if self.doc.needs_pass and self.password:
            self.doc.authenticate(self.password)
        self.page_no = min(self.page_no, self.doc.page_count - 1)
        self.view.sel_annot_xref = None
        self.modified = True
        self.build_thumbs()
        self.render(keep_scroll=True)
        self.update_ui()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.doc.tobytes(garbage=0))
        self._restore_bytes(self.undo_stack.pop())
        self.msg("Undone")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.doc.tobytes(garbage=0))
        self._restore_bytes(self.redo_stack.pop())
        self.msg("Redone")

    def cur_page(self) -> fitz.Page:
        return self.doc[self.page_no]

    # ------------------------------------------------------------------ open / save
    def maybe_save(self):
        if not self.doc or not self.modified:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to this document?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def open_dialog(self):
        start = self.settings.value("lastdir", os.path.expanduser("~/Documents"))
        f, _ = QFileDialog.getOpenFileName(self, "Open", start, OPEN_FILTER)
        if f:
            self.open_file(f)

    def open_file(self, path):
        if not os.path.exists(path):
            self.error(f"File not found:\n{path}")
            return
        if not self.maybe_save():
            return
        password = None
        try:
            probe = fitz.open(path)
            needs = probe.needs_pass
            probe.close()
            if needs:
                password, ok = QInputDialog.getText(self, "Password", f"{os.path.basename(path)} is protected.\nPassword:",
                                                    QLineEdit.Password)
                if not ok:
                    return
            doc = core.open_document(path, password)
        except PermissionError:
            self.error("Wrong password.")
            return
        except Exception as e:
            self.error(f"Could not open the file:\n{e}")
            return
        if self.doc:
            self.doc.close()
        self.doc = doc
        self.password = password
        is_pdf = path.lower().endswith(".pdf")
        self.path = path if is_pdf else None
        self.modified = not is_pdf
        self.page_no = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.search_hits, self.search_index = [], -1
        self.search_info.setText("")
        self.encrypt = None
        self.settings.setValue("lastdir", os.path.dirname(path))
        self._add_recent(path)
        self.build_thumbs()
        self.render()
        self.update_ui()
        if not is_pdf:
            self.msg("Converted to PDF - use Save As to keep it.", 8000)

    def new_blank(self):
        if not self.maybe_save():
            return
        if self.doc:
            self.doc.close()
        self.doc = fitz.open()
        self.doc.new_page(width=595, height=842)  # A4
        self.path, self.password, self.modified, self.page_no = None, None, True, 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.build_thumbs()
        self.render()
        self.update_ui()

    def close_doc(self):
        if not self.maybe_save():
            return
        if self.doc:
            self.doc.close()
        self.doc, self.path, self.modified = None, None, False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.thumbs.clear()
        self.view.clear_page()
        self.update_ui()

    def save(self):
        if not self.doc:
            return False
        if not self.path:
            return self.save_as()
        return self._save_to(self.path)

    def save_as(self):
        if not self.doc:
            return False
        start = self.path or os.path.join(self.settings.value("lastdir", ""), "document.pdf")
        f, _ = QFileDialog.getSaveFileName(self, "Save As", start, "PDF (*.pdf)")
        if not f:
            return False
        if not f.lower().endswith(".pdf"):
            f += ".pdf"
        return self._save_to(f)

    def _save_to(self, path):
        try:
            enc = self.encrypt or ("", "", None)
            if not self.encrypt and self.password and self.doc.is_encrypted is False:
                pass
            out = core.save_document(self.doc, path, enc[0], enc[1], enc[2])
            if out != path:  # saved to temp because the file is open; swap it in
                data_page = self.page_no
                self.doc.close()
                os.replace(out, path)
                self.doc = fitz.open(path)
                if self.doc.needs_pass:
                    self.doc.authenticate(enc[0] or enc[1] or self.password or "")
                self.page_no = min(data_page, self.doc.page_count - 1)
            else:
                # keep working on the saved file
                pno = self.page_no
                self.doc.close()
                self.doc = fitz.open(path)
                if self.doc.needs_pass:
                    self.doc.authenticate(enc[0] or enc[1] or self.password or "")
                self.page_no = pno
            if self.encrypt:
                self.password = enc[0] or enc[1]
            self.path = path
            self.modified = False
            self._add_recent(path)
            self.render(keep_scroll=True)
            self.update_ui()
            self.msg(f"Saved {path}")
            return True
        except Exception as e:
            traceback.print_exc()
            self.error(f"Could not save:\n{e}\n\nIs the file open in another program?")
            return False

    def _add_recent(self, path):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [path] + [r for r in rec if r != path]
        self.settings.setValue("recent", rec[:12])

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            a = self.recent_menu.addAction(r)
            a.triggered.connect(lambda checked=False, p=r: self.open_file(p))
        if not rec:
            self.recent_menu.addAction("(empty)").setEnabled(False)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not files:
            return
        if len(files) == 1 or not self.doc:
            self.open_file(files[0])
            for f in files[1:]:
                MainWindow(f).show()
        else:
            r = QMessageBox.question(self, APP_NAME, f"Insert {len(files)} files after page {self.page_no + 1}?")
            if r == QMessageBox.Yes:
                for f in files:
                    self._insert_from(f)

    # ------------------------------------------------------------------ view
    def render(self, keep_scroll=False):
        if not self.doc:
            return
        self.page_no = max(0, min(self.page_no, self.doc.page_count - 1))
        page = self.cur_page()
        self.zoom = self.compute_zoom(page)
        self.view.show_page(page, self.zoom, keep_scroll)

    def compute_zoom(self, page):
        if self.zoom_mode == "custom":
            return self.zoom
        vw = self.view.viewport().width() - 50
        vh = self.view.viewport().height() - 50
        r = page.rect
        if self.zoom_mode == "width":
            return max(0.1, vw / r.width)
        return max(0.1, min(vw / r.width, vh / r.height))

    def set_zoom(self, z):
        self.zoom_mode = "custom"
        self.zoom = max(0.1, min(8.0, z))
        self.render(keep_scroll=True)
        self.update_ui()

    def set_zoom_mode(self, mode):
        self.zoom_mode = mode
        self.zoom_box.setCurrentIndex(0 if mode == "width" else 1)
        self.render()
        self.update_ui()

    def _zoom_box_changed(self, *_):
        t = self.zoom_box.currentText().strip().lower()
        if t.startswith("fit w"):
            self.set_zoom_mode("width")
        elif t.startswith("fit p"):
            self.set_zoom_mode("page")
        else:
            try:
                self.set_zoom(float(t.rstrip("%")) / 100)
            except ValueError:
                pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.doc and self.zoom_mode != "custom":
            QTimer.singleShot(50, self.render)

    def go_page(self, n):
        if not self.doc:
            return
        n = max(0, min(n, self.doc.page_count - 1))
        if n == self.page_no and self.view.page is not None and self.view.page.number == n:
            return
        self.page_no = n
        self.view.sel_annot_xref = None
        self.render()
        self.thumbs.blockSignals(True)
        self.thumbs.setCurrentRow(n)
        self.thumbs.blockSignals(False)
        self.thumbs.scrollToItem(self.thumbs.item(n))
        self.update_ui()

    def step_page(self, d):
        if not self.doc:
            return
        n = self.page_no + d
        if 0 <= n < self.doc.page_count:
            self.go_page(n)
            vs = self.view.verticalScrollBar()
            vs.setValue(vs.minimum() if d > 0 else vs.maximum())

    def _thumb_clicked(self, row):
        if row >= 0:
            self.go_page(row)

    # ------------------------------------------------------------------ thumbnails
    def build_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        placeholder = QPixmap(100, 140)
        placeholder.fill(QColor("#ffffff"))
        for i in range(self.doc.page_count):
            it = QListWidgetItem(QIcon(placeholder), "")
            it.setData(Qt.UserRole, i)
            it.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(it)
        self.thumbs.setCurrentRow(self.page_no)
        self.thumbs.blockSignals(False)
        self._thumb_queue = list(range(self.doc.page_count))
        self._thumb_timer.start(0)

    def _render_thumb_batch(self):
        if not self.doc or not self._thumb_queue:
            self._thumb_timer.stop()
            return
        for _ in range(4):
            if not self._thumb_queue:
                break
            self.refresh_thumb(self._thumb_queue.pop(0))

    def refresh_thumb(self, i):
        if not self.doc or i >= self.thumbs.count():
            return
        page = self.doc[i]
        z = 160 / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = pix_to_qimage(pix)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        label = str(i + 1)
        f = QFont("Segoe UI", 8, QFont.Bold)
        p.setFont(f)
        bw = 12 + 7 * len(label)
        box = QRectF((img.width() - bw) / 2, img.height() - 20, bw, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(45, 55, 72, 200))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, label)
        p.end()
        item = self.thumbs.item(i)
        item.setIcon(QIcon(QPixmap.fromImage(img)))
        item.setToolTip(f"Page {i + 1}")

    def _thumb_menu(self, pos):
        if not self.doc:
            return
        m = QMenu(self)
        m.addActions([self.a_rotl, self.a_rotr, self.a_delpage, self.a_dup, self.a_extract])
        m.addSeparator()
        m.addActions([self.a_blank, self.a_insfile])
        m.exec(self.thumbs.mapToGlobal(pos))

    def selected_pages(self):
        s = self.thumbs.selected_pages()
        return s if s else [self.page_no]

    # ------------------------------------------------------------------ tools
    def set_tool(self, key):
        if key == "sign" and self.doc:
            d = dialogs.SignatureDialog(self)
            if not d.exec() or not d.result_path:
                key = "select"
            else:
                self.signature_file = d.result_path
                self.msg("Now drag a box on the page where the signature should go.", 8000)
        self.current_tool = key
        self.tool_actions[key].setChecked(True)
        self.view.update_cursor()
        if self.view.hover_item:
            self.view.scene_.removeItem(self.view.hover_item)
            self.view.hover_item = None
        self.update_ui()

    def _annot_common(self, a, stroke=True, fill=None):
        if stroke:
            a.set_colors(stroke=rgb_tuple(self.color), fill=fill)
        a.set_border(width=self.width)
        a.set_opacity(self.opacity)
        a.update()

    def add_point_item(self, kind, pt):
        page = self.cur_page()
        if kind == "note":
            text, ok = QInputDialog.getMultiLineText(self, "Sticky note", "Comment:")
            if not ok or not text:
                return
            self.checkpoint()
            a = page.add_text_annot(pt, text, icon="Comment")
            a.set_colors(stroke=rgb_tuple(self.color))
            a.set_info(title=os.environ.get("USERNAME", "PDF Studio"))
            a.update()
        else:
            d = dialogs.TextDialog(self, "Add text", "", self.fontsize, self.color)
            if not d.exec() or not d.text().strip():
                return
            text, size, color = d.text(), d.size.value(), d.color.color()
            self.checkpoint()
            lines = text.splitlines() or [text]
            w = max(fitz.get_text_length(l, "helv", size) for l in lines) + size * 0.8 + 6
            h = len(lines) * size * 1.25 + size * 0.6 + 4
            vis = pt * page.rotation_matrix
            r = fitz.Rect(vis.x, vis.y, vis.x + w, vis.y + h) * page.derotation_matrix
            r.normalize()
            a = page.add_freetext_annot(r, text, fontsize=size, fontname="helv",
                                        text_color=rgb_tuple(color), rotate=page.rotation)
            a.set_border(width=0)
            a.set_opacity(self.opacity)
            a.update()
            self.color_btn.set_color(color)
            self.color = color
        self.changed()

    def add_rect_item(self, kind, rect, small):
        page = self.cur_page()
        if kind in ("highlight", "underline", "strike"):
            words = [fitz.Rect(w[:4]) for w in page.get_text("words")
                     if fitz.Rect(w[:4]).intersects(rect)]
            if not words:
                self.msg("No text under the selection.")
                return
            self.checkpoint()
            quads = [w.quad for w in words]
            fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                  "strike": page.add_strikeout_annot}[kind]
            a = fn(quads)
            col = self.color
            if kind == "highlight":
                r, g, b = rgb_tuple(col)
                if 0.299 * r + 0.587 * g + 0.114 * b < 0.6:
                    col = "#ffeb3b"  # dark colours would hide the text - use marker yellow
            a.set_colors(stroke=rgb_tuple(col))
            a.set_opacity(self.opacity)
            a.update()
        elif kind in ("rect", "ellipse"):
            if small:
                return
            self.checkpoint()
            a = page.add_rect_annot(rect) if kind == "rect" else page.add_circle_annot(rect)
            self._annot_common(a)
        elif kind in ("image", "sign"):
            if kind == "image":
                f, _ = QFileDialog.getOpenFileName(self, "Insert image", self.settings.value("lastdir", ""),
                                                   "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
                if not f:
                    return
            else:
                f = self.signature_file
                if not f:
                    return
            if small:
                img = fitz.Pixmap(f)
                w = 200 if kind == "image" else 160
                h = w * img.height / max(1, img.width)
                vis = fitz.Point(rect.x0, rect.y0) * page.rotation_matrix
                rect = fitz.Rect(vis.x, vis.y, vis.x + w, vis.y + h) * page.derotation_matrix
                rect.normalize()
            self.checkpoint()
            page.insert_image(rect, filename=f, keep_proportion=True, rotate=page.rotation)
            if kind == "sign":
                self.set_tool("select")
        elif kind == "whiteout":
            if small:
                return
            self.checkpoint()
            page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
        elif kind == "redact":
            if small:
                return
            r = QMessageBox.warning(self, "Redact", "Permanently remove all text, images and graphics in this area?\n"
                                    "(You can still Undo before saving.)", QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
            self.checkpoint()
            page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions()
        else:
            return
        self.changed()

    def add_line(self, a, b, arrow=False):
        self.checkpoint()
        an = self.cur_page().add_line_annot(a, b)
        if arrow:
            an.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            an.set_colors(stroke=rgb_tuple(self.color), fill=rgb_tuple(self.color))
            an.set_border(width=self.width)
            an.set_opacity(self.opacity)
            an.update()
        else:
            self._annot_common(an)
        self.changed()

    def add_ink(self, strokes):
        self.checkpoint()
        a = self.cur_page().add_ink_annot(strokes)
        self._annot_common(a)
        self.changed()

    def edit_text_line(self, ln):
        col = "#%06x" % ln["color"]
        d = dialogs.TextDialog(self, "Edit text", ln["text"], round(ln["size"], 1), col,
                               show_font=True, multiline=False)
        if not d.exec():
            return
        new = d.text()
        if new == ln["text"] and abs(d.size.value() - ln["size"]) < 0.05 and d.color.color() == col \
                and not d.fontfile():
            return
        self.checkpoint()
        try:
            core.replace_text(self.cur_page(), ln, new, d.size.value(), d.color.color(), d.fontfile())
        except Exception as e:
            self.undo_stack.pop()
            self.error(f"Could not edit this text:\n{e}")
            return
        self.changed()

    # annotations
    def annot_menu(self, a, gpos):
        m = QMenu(self)
        m.addAction("Edit / Properties...", lambda: self.edit_annot(a))
        m.addAction("Change colour to current", lambda: self._recolor(a))
        m.addSeparator()
        m.addAction("Delete", self.delete_selected_annot)
        m.exec(gpos)

    def _recolor(self, a):
        self.checkpoint()
        a = self.view.find_annot_by_xref(a.xref)
        t = a.type[0]
        if t == fitz.PDF_ANNOT_FREE_TEXT:
            a.update(text_color=rgb_tuple(self.color))
        else:
            a.set_colors(stroke=rgb_tuple(self.color))
            a.update()
        self.changed()

    def edit_annot(self, a):
        t = a.type[0]
        info = a.info
        if t in (fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_TEXT):
            text, ok = QInputDialog.getMultiLineText(self, "Edit", "Text:", info.get("content", ""))
            if not ok:
                return
            self.checkpoint()
            a = self.view.find_annot_by_xref(a.xref)
            a.set_info(content=text)
            a.update()
            self.changed()
        else:
            text, ok = QInputDialog.getMultiLineText(self, "Comment", "Comment on this mark-up:",
                                                     info.get("content", ""))
            if ok:
                self.checkpoint()
                a = self.view.find_annot_by_xref(a.xref)
                a.set_info(content=text)
                a.update()
                self.changed()

    def move_annot(self, a, dv):
        self.checkpoint()
        try:
            t = a.type[0]
            if t in (fitz.PDF_ANNOT_INK,):
                strokes = [[(x + dv.x, y + dv.y) for x, y in s] for s in a.vertices]
                info, col, border, op = a.info, a.colors, a.border, a.opacity
                self.cur_page().delete_annot(a)
                n = self.cur_page().add_ink_annot(strokes)
                n.set_colors(stroke=col.get("stroke"))
                n.set_border(width=border.get("width", 1))
                n.set_opacity(op if op >= 0 else 1)
                n.set_info(info)
                n.update()
                self.view.sel_annot_xref = n.xref
            elif t == fitz.PDF_ANNOT_LINE:
                (p1, p2) = a.vertices[:2]
                ends, col, border, op = a.line_ends, a.colors, a.border, a.opacity
                self.cur_page().delete_annot(a)
                n = self.cur_page().add_line_annot(fitz.Point(p1) + dv, fitz.Point(p2) + dv)
                n.set_line_ends(*ends)
                n.set_colors(stroke=col.get("stroke"), fill=col.get("fill"))
                n.set_border(width=border.get("width", 1))
                n.set_opacity(op if op >= 0 else 1)
                n.update()
                self.view.sel_annot_xref = n.xref
            elif t in (fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE,
                       fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_SQUIGGLY):
                self.undo_stack.pop()
                self.msg("Text mark-ups stay with their text and can't be moved.")
                self.view.draw_overlays()
                return
            else:
                a.set_rect(a.rect + (dv.x, dv.y, dv.x, dv.y))
                a.update()
        except Exception as e:
            self.undo_stack.pop()
            self.msg(f"Can't move this item: {e}")
            self.view.draw_overlays()
            return
        self.changed()

    def delete_selected_annot(self):
        if not self.doc or not self.view.sel_annot_xref:
            return
        a = self.view.find_annot_by_xref(self.view.sel_annot_xref)
        if not a:
            return
        self.checkpoint()
        self.cur_page().delete_annot(a)
        self.view.sel_annot_xref = None
        self.changed()

    def remove_all_annots(self):
        if QMessageBox.question(self, APP_NAME, "Remove ALL comments, highlights and drawings from every page?") \
                != QMessageBox.Yes:
            return
        self.checkpoint()
        n = 0
        for page in self.doc:
            for a in list(page.annots()):
                page.delete_annot(a)
                n += 1
        self.changed(rebuild_thumbs=True)
        self.msg(f"Removed {n} comments.")

    def flatten(self):
        self.checkpoint()
        try:
            self.doc.bake(annots=True, widgets=True)
        except Exception:
            data = fitz.open()
            for page in self.doc:
                pg = data.new_page(width=page.rect.width, height=page.rect.height)
                pg.show_pdf_page(pg.rect, self.doc, page.number)
            self.doc.close()
            self.doc = fitz.open("pdf", data.tobytes())
        self.changed(rebuild_thumbs=True)
        self.msg("Comments and form fields are now part of the page.")

    # ------------------------------------------------------------------ pages
    def rotate_pages(self, deg):
        self.checkpoint()
        for p in self.selected_pages():
            pg = self.doc[p]
            pg.set_rotation((pg.rotation + deg) % 360)
            self.refresh_thumb(p)
        self.modified = True
        self.render()
        self.update_ui()

    def delete_pages(self):
        pages = self.selected_pages()
        if len(pages) >= self.doc.page_count:
            self.error("A PDF must keep at least one page.")
            return
        if QMessageBox.question(self, APP_NAME, f"Delete {len(pages)} page(s)?") != QMessageBox.Yes:
            return
        self.checkpoint()
        self.doc.delete_pages(pages)
        self.page_no = min(pages[0], self.doc.page_count - 1)
        self.changed(rebuild_thumbs=True)

    def insert_blank(self):
        self.checkpoint()
        r = self.cur_page().rect
        self.doc.new_page(self.page_no + 1, width=r.width, height=r.height)
        self.page_no += 1
        self.changed(rebuild_thumbs=True)

    def duplicate_page(self):
        self.checkpoint()
        self.doc.fullcopy_page(self.page_no, self.page_no + 1)
        self.page_no += 1
        self.changed(rebuild_thumbs=True)

    def insert_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Insert pages from", self.settings.value("lastdir", ""),
                                                INSERT_FILTER)
        for f in files:
            self._insert_from(f)

    def _insert_from(self, f):
        try:
            src = core.open_document(f)
        except PermissionError:
            pw, ok = QInputDialog.getText(self, "Password", f"Password for {os.path.basename(f)}:", QLineEdit.Password)
            if not ok:
                return
            try:
                src = core.open_document(f, pw)
            except Exception as e:
                self.error(str(e))
                return
        except Exception as e:
            self.error(f"Could not open {f}:\n{e}")
            return
        self.checkpoint()
        at = self.page_no + 1
        self.doc.insert_pdf(src, start_at=at)
        n = src.page_count
        src.close()
        self.page_no = at
        self.changed(rebuild_thumbs=True)
        self.msg(f"Inserted {n} page(s).")

    def extract_pages(self):
        pages = self.selected_pages()
        start = os.path.join(self.settings.value("lastdir", ""), "extracted.pdf")
        f, _ = QFileDialog.getSaveFileName(self, "Save extracted pages", start, "PDF (*.pdf)")
        if not f:
            return
        core.subset(self.doc, pages).save(f, garbage=3, deflate=True)
        self.msg(f"Saved {len(pages)} page(s) to {f}")

    def move_page(self, d):
        n = self.page_no + d
        if not (0 <= n < self.doc.page_count):
            return
        self.checkpoint()
        order = list(range(self.doc.page_count))
        order[self.page_no], order[n] = order[n], order[self.page_no]
        self.doc.select(order)
        self.page_no = n
        self.changed(rebuild_thumbs=True)

    def reorder_pages(self, order):
        self.checkpoint()
        cur = self.page_no
        self.doc.select(order)
        self.page_no = order.index(cur) if cur in order else 0
        self.changed(rebuild_thumbs=True)

    def reverse_pages(self):
        self.checkpoint()
        self.doc.select(list(range(self.doc.page_count - 1, -1, -1)))
        self.changed(rebuild_thumbs=True)

    def crop_hint(self):
        page = self.cur_page()
        r = page.cropbox
        text, ok = QInputDialog.getText(
            self, "Crop page", "Margins to cut in mm: left, top, right, bottom\n(applies to selected pages)",
            text="10, 10, 10, 10")
        if not ok:
            return
        try:
            l, t, rr, b = [float(x) * 72 / 25.4 for x in text.replace(";", ",").split(",")]
        except ValueError:
            self.error("Enter four numbers, e.g. 10, 10, 10, 10")
            return
        self.checkpoint()
        for p in self.selected_pages():
            pg = self.doc[p]
            mb = pg.mediabox
            cb = pg.cropbox
            new = fitz.Rect(cb.x0 + l, cb.y0 + t, cb.x1 - rr, cb.y1 - b) & mb
            if new.is_empty:
                continue
            pg.set_cropbox(new)
        self.changed(rebuild_thumbs=True)

    # ------------------------------------------------------------------ document tools
    def merge_files(self):
        initial = [self.path] if self.path and not self.modified else []
        d = dialogs.FileListDialog(self, "Merge / Combine files",
                                   "PDF and images (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.xps *.epub)",
                                   initial)
        if not d.exec() or len(d.files()) < 1:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save combined PDF",
                                           os.path.join(self.settings.value("lastdir", ""), "combined.pdf"),
                                           "PDF (*.pdf)")
        if not f:
            return
        self.run_task("Combining files...", core.files_to_pdf, d.files(), f,
                      done=lambda _: self._offer_open(f))

    def images_to_pdf(self):
        d = dialogs.FileListDialog(self, "Images to PDF",
                                   "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
        if not d.exec() or not d.files():
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save PDF", os.path.join(self.settings.value("lastdir", ""),
                                                                          "images.pdf"), "PDF (*.pdf)")
        if f:
            self.run_task("Creating PDF...", core.files_to_pdf, d.files(), f,
                          done=lambda _: self._offer_open(f))

    def _offer_open(self, f):
        if QMessageBox.question(self, APP_NAME, f"Created:\n{f}\n\nOpen it now?") == QMessageBox.Yes:
            self.open_file(f)

    def split_doc(self):
        d = dialogs.SplitDialog(self, self.doc.page_count)
        if not d.exec():
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the split files",
                                                  self.settings.value("lastdir", ""))
        if not folder:
            return
        mode, val = d.mode()
        try:
            files = core.split_pdf(self.doc, folder, mode, val, self._name())
        except Exception as e:
            self.error(str(e))
            return
        self._done_files(files)

    def watermark(self):
        d = dialogs.WatermarkDialog(self, self.doc.page_count)
        if not d.exec():
            return
        try:
            pages = d.pages()
        except ValueError as e:
            self.error(str(e))
            return
        self.checkpoint()
        core.add_watermark(self.doc, d.text.text(), pages, d.size.value(), d.color.color(),
                           d.opacity.value() / 100, d.angle.value(), d.image.text() or None)
        self.changed(rebuild_thumbs=True)

    def page_numbers(self):
        d = dialogs.PageNumberDialog(self, self.doc.page_count)
        if not d.exec():
            return
        try:
            pages = d.pages()
        except ValueError as e:
            self.error(str(e))
            return
        self.checkpoint()
        if d.enable_nums.isChecked():
            core.add_page_numbers(self.doc, pages, d.fmt.currentText(), d.pos.currentData(),
                                  d.size.value(), d.color.color(), start=d.start.value())
        if d.header.text() or d.footer.text():
            pos = d.pos.currentData() if d.enable_nums.isChecked() else ""
            # keep header/footer clear of page numbers on the same edge
            core.add_header_footer(self.doc, pages, d.header.text() if not pos.startswith("top") else "",
                                   d.footer.text() if not pos.startswith("bottom") else "",
                                   d.size.value(), d.color.color())
            core.add_header_footer(self.doc, pages, d.header.text() if pos.startswith("top") else "",
                                   d.footer.text() if pos.startswith("bottom") else "",
                                   d.size.value(), d.color.color(), margin=24 + d.size.value() * 1.8)
        self.changed(rebuild_thumbs=True)

    def protect(self):
        d = dialogs.ProtectDialog(self)
        if not d.exec():
            return
        self.encrypt = (d.user.text(), d.owner.text(), d.permissions())
        self.modified = True
        self.update_ui()
        if self.save_as():
            QMessageBox.information(self, APP_NAME, "The saved PDF is now password protected (AES-256).")

    def unprotect(self):
        if not self.doc.is_encrypted and not self.password and not self.encrypt:
            self.msg("This document has no password.")
            return
        self.encrypt = None
        self.modified = True
        self.update_ui()
        if self.save_as():
            self.password = None
            QMessageBox.information(self, APP_NAME, "Saved without password.")

    def compress(self):
        dpi, ok = QInputDialog.getInt(self, "Compress PDF",
                                      "Image resolution (dpi). Lower = smaller file.\n"
                                      "72 = screen, 150 = email/good, 220 = print", 150, 50, 600)
        if not ok:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save compressed PDF",
                                           os.path.join(self.settings.value("lastdir", ""),
                                                        self._name() + "_compressed.pdf"), "PDF (*.pdf)")
        if not f:
            return
        before = len(self.doc.tobytes(garbage=3, deflate=True))
        pages = list(range(self.doc.page_count))

        def done(_):
            after = os.path.getsize(f)
            QMessageBox.information(self, APP_NAME, f"Saved {f}\n\n{before / 1048576:.2f} MB → {after / 1048576:.2f} MB "
                                    f"({(1 - after / max(1, before)) * 100:.0f}% smaller)")
        self.run_task("Compressing...", core.export_pdf_compressed, self.doc, f, pages=pages, dpi=dpi,
                      quality=75, done=done)

    def properties(self):
        m = self.doc.metadata or {}
        p = self.cur_page()
        size = os.path.getsize(self.path) if self.path and os.path.exists(self.path) else None
        info = [("File", self.path or "(not saved)"),
                ("File size", f"{size / 1048576:.2f} MB" if size else "-"),
                ("Pages", self.doc.page_count),
                ("Page size", f"{p.rect.width:.0f} × {p.rect.height:.0f} pt"),
                ("PDF version", m.get("format", "")),
                ("Encrypted", m.get("encryption") or "No"),
                ("Created", m.get("creationDate", "")), ("Modified", m.get("modDate", ""))]
        d = dialogs.MetadataDialog(self, m, info)
        if d.exec():
            self.checkpoint()
            self.doc.set_metadata({k: v for k, v in d.metadata(m).items()
                                   if k in ("title", "author", "subject", "keywords", "creator",
                                            "producer", "creationDate", "modDate")})
            self.modified = True
            self.update_ui()

    # ------------------------------------------------------------------ export
    def _name(self):
        return os.path.splitext(os.path.basename(self.path))[0] if self.path else "document"

    def export_dialog(self):
        d = dialogs.ExportDialog(self, self.doc.page_count, self.page_no)
        if not d.exec():
            return
        key = d.key()
        try:
            pages = d.pages()
        except ValueError as e:
            self.error(str(e))
            return
        label, ext = core.EXPORT_FORMATS[key]
        lastdir = self.settings.value("exportdir", self.settings.value("lastdir", ""))
        if ext:
            suffix = ext[1:]
            default = os.path.join(lastdir, self._name() + ("_compressed" if key == "pdf_compressed" else "") + suffix)
            target, _ = QFileDialog.getSaveFileName(self, "Export as", default, label)
            if not target:
                return
            if not target.lower().endswith(suffix):
                target += suffix
            self.settings.setValue("exportdir", os.path.dirname(target))
        else:
            parent = QFileDialog.getExistingDirectory(self, "Choose a folder for the files", lastdir)
            if not parent:
                return
            self.settings.setValue("exportdir", parent)
            target = os.path.join(parent, f"{self._name()}_{key}")
        self.run_task(f"Exporting {label.split('(')[0].strip()}...", core.export, self.doc, key, target,
                      pages, dpi=d.dpi.value(), jpg_quality=d.quality.value(), name=self._name(),
                      done=self._done_files)

    def _done_files(self, files):
        if not files:
            QMessageBox.information(self, APP_NAME, "Nothing was found to export.")
            return
        first = files[0]
        folder = os.path.dirname(first)
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Done - {len(files)} file(s) created." if len(files) > 1 else f"Done:\n{first}")
        b_open = box.addButton("Open file" if len(files) == 1 else "Open folder", QMessageBox.AcceptRole)
        b_folder = box.addButton("Show in folder", QMessageBox.ActionRole) if len(files) == 1 else None
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() == b_open:
            open_path(first if len(files) == 1 else folder)
        elif b_folder is not None and box.clickedButton() == b_folder:
            show_in_folder(first)

    def run_task(self, title, fn, *args, done=None, **kwargs):
        import multiprocessing as mp
        import queue as _queue
        import tempfile
        temps = []
        call_args = []
        for a in args:
            if isinstance(a, fitz.Document):
                fd, tmp = tempfile.mkstemp(suffix=".pdf")
                os.close(fd)
                a.save(tmp)
                temps.append(tmp)
                call_args.append(("__doc__", tmp))
            else:
                call_args.append(a)
        dlg = QProgressDialog(title, None, 0, 0, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setCancelButton(None)
        dlg.show()
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        proc = ctx.Process(target=_child_task, args=(fn.__name__, call_args, kwargs, q), daemon=True)
        proc.start()
        timer = QTimer(self)
        state = {"done": False}

        def finish():
            state["done"] = True
            timer.stop()
            dlg.close()
            for t in temps:
                try:
                    os.remove(t)
                except OSError:
                    pass

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "p":
                        dlg.setMaximum(msg[2])
                        dlg.setValue(msg[1])
                    elif msg[0] == "ok":
                        finish()
                        proc.join(2)
                        if done:
                            done(msg[1])
                        return
                    else:
                        finish()
                        self.error(f"Failed:\n{msg[1]}")
                        return
            except _queue.Empty:
                pass
            if not proc.is_alive() and not state["done"]:
                # give the queue a moment to flush
                state.setdefault("dead", 0)
                state["dead"] += 1
                if state["dead"] > 10:
                    finish()
                    self.error("The conversion stopped unexpectedly (exit code %s)." % proc.exitcode)
        timer.timeout.connect(poll)
        timer.start(100)
        self._threads.append(proc)

    # ------------------------------------------------------------------ search
    def do_search(self):
        if not self.doc:
            return
        q = self.search_box.text().strip()
        self.search_hits = core.search(self.doc, q)
        self.search_index = -1
        if not self.search_hits:
            self.search_info.setText("  no matches " if q else "")
            self.view.draw_overlays()
            return
        # first hit at or after the current page
        after = [i for i, (p, _) in enumerate(self.search_hits) if p >= self.page_no]
        self.search_index = after[0] if after else 0
        self._show_hit()

    def next_hit(self, d):
        if not self.search_hits:
            return self.do_search()
        self.search_index = (self.search_index + d) % len(self.search_hits)
        self._show_hit()

    def _show_hit(self):
        p, r = self.search_hits[self.search_index]
        self.search_info.setText(f"  {self.search_index + 1}/{len(self.search_hits)} ")
        if p != self.page_no:
            self.go_page(p)
        self.view.draw_overlays()
        self.view.ensureVisible(self.view.to_scene_rect(r), 60, 120)

    def copy_page_text(self):
        QApplication.clipboard().setText(self.cur_page().get_text(sort=True))
        self.msg("Page text copied to clipboard.")

    # ------------------------------------------------------------------ print
    def print_doc(self):
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self._name())
        printer.setFromTo(1, self.doc.page_count)
        dlg = QPrintDialog(printer, self)
        dlg.setMinMax(1, self.doc.page_count)
        if dlg.exec() != QPrintDialog.Accepted:
            return
        if printer.printRange() == QPrinter.PageRange:
            pages = list(range(printer.fromPage() - 1, printer.toPage()))
        elif printer.printRange() == QPrinter.CurrentPage:
            pages = [self.page_no]
        else:
            pages = list(range(self.doc.page_count))
        dpi = min(300, printer.resolution())
        painter = QPainter()
        if not painter.begin(printer):
            self.error("Could not start the printer.")
            return
        for i, p in enumerate(pages):
            if i:
                printer.newPage()
            pix = self.doc[p].get_pixmap(dpi=dpi, alpha=False)
            img = pix_to_qimage(pix)
            area = painter.viewport()
            size = img.size()
            size.scale(area.size(), Qt.KeepAspectRatio)
            painter.setViewport(area.x(), area.y(), size.width(), size.height())
            painter.setWindow(img.rect())
            painter.drawImage(0, 0, img)
            painter.setViewport(area)
            QApplication.processEvents()
        painter.end()
        self.msg(f"Sent {len(pages)} page(s) to the printer.")

    # ------------------------------------------------------------------ misc
    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<h3>{APP_NAME} {VERSION}</h3>"
                          "<p>View, edit, sign, protect and convert PDF files.</p>"
                          f"<p>PDF engine: PyMuPDF {fitz.VersionBind} / MuPDF {fitz.VersionFitz}</p>")

    def shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Ctrl+O  Open          Ctrl+S  Save          Ctrl+E  Export\n"
            "Ctrl+Z  Undo          Ctrl+Y  Redo          Ctrl+P  Print\n"
            "Ctrl+F  Find          F3 / Shift+F3  Next / previous match\n"
            "PgUp / PgDn  Page     Home / End  First / last page\n"
            "Ctrl + mouse wheel  Zoom       Ctrl+1 Fit width   Ctrl+2 Fit page\n"
            "Esc  Select tool      Delete  Delete selected comment\n"
            "F4  Show/hide page thumbnails\n\n"
            "Drag thumbnails to reorder pages. Right-click thumbnails for page tools.\n"
            "Drag files onto the window to open or insert them."))

    def closeEvent(self, e):
        if not self.maybe_save():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("color", self.color)
        self.settings.setValue("width", self.width)
        self.settings.setValue("fontsize", self.fontsize)
        for p in list(self._threads):
            if p.is_alive():
                p.terminate()
        if self in MainWindow.windows:
            MainWindow.windows.remove(self)
        e.accept()


def open_path(p):
    if sys.platform == "win32":
        os.startfile(p)  # noqa
    else:
        subprocess.Popen(["xdg-open", p])


def show_in_folder(p):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    else:
        open_path(os.path.dirname(p))


STYLE = """
QMainWindow, QDialog { background: #f6f7f9; }
QToolBar { background: #ffffff; border: none; border-bottom: 1px solid #dde1e7; spacing: 2px; padding: 3px; }
QToolButton { border: 1px solid transparent; border-radius: 6px; padding: 3px 5px; color: #2d3748; }
QToolButton:hover { background: #edf2f7; border-color: #d5dbe3; }
QToolButton:checked { background: #dbeafe; border-color: #93c5fd; color: #1e40af; }
QToolButton:disabled { color: #a0aec0; }
QMenuBar { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
QMenuBar::item:selected { background: #edf2f7; }
QMenu { background: #ffffff; border: 1px solid #d5dbe3; padding: 4px; }
QMenu::item { padding: 5px 26px 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: #dbeafe; color: #1e3a8a; }
QDockWidget::title { background: #e9edf2; padding: 5px; font-weight: 600; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
  background: #fff; border: 1px solid #cbd2db; border-radius: 5px; padding: 3px 5px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #3b82f6; }
QPushButton { background: #ffffff; border: 1px solid #cbd2db; border-radius: 5px; padding: 5px 14px; }
QPushButton:hover { background: #edf2f7; }
QPushButton:default { background: #2563eb; color: white; border-color: #1d4ed8; }
QStatusBar { background: #ffffff; border-top: 1px solid #e2e8f0; color: #4a5568; }
"""


def selftest(pdf, folder):
    """Convert `pdf` to every format (used to verify a fresh build)."""
    import multiprocessing as mp
    os.makedirs(folder, exist_ok=True)
    doc = core.open_document(pdf)
    pages = list(range(doc.page_count))
    bad = 0
    lines = []
    for key, (_, ext) in core.EXPORT_FORMATS.items():
        target = os.path.join(folder, "selftest" + ext[1:]) if ext else os.path.join(folder, "selftest_" + key)
        try:
            files = core.export(doc, key, target, pages, name="selftest")
            lines.append(f"OK    {key:15} {len(files)} file(s)")
        except Exception as e:
            bad += 1
            lines.append(f"FAIL  {key:15} {type(e).__name__}: {e}")
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child_task, args=("export", [("__doc__", pdf), "txt",
                    os.path.join(folder, "selftest_proc.txt"), pages], {}, q))
    p.start()
    p.join(120)
    res = None
    while not q.empty():
        res = q.get()
    lines.append(("OK    " if res and res[0] == "ok" else "FAIL  ") + "background process")
    bad += 0 if res and res[0] == "ok" else 1
    with open(os.path.join(folder, "selftest_report.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if bad else 0


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    for name in ("stdout", "stderr"):  # windowed .exe has no console
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFStudio.App.1")
        except Exception:
            pass
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2], sys.argv[3]))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PDFStudio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 9))
    app.setWindowIcon(app_icon())
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if files:
        for f in files:
            MainWindow(f).show()
    else:
        MainWindow().show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
