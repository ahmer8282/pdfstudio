# PyInstaller build recipe for PDF Studio  (used by build_exe.bat)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("icon.ico", ".")]
hidden = []
for pkg in ("pymupdf", "pymupdf4llm", "pdf2docx", "pptx", "docx"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass
hidden += collect_submodules("pdf2docx") + collect_submodules("pymupdf4llm")

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hidden + ["openpyxl", "PIL.TiffImagePlugin", "PySide6.QtPrintSupport"],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "torch", "sklearn", "IPython", "PyQt5", "PyQt6",
              "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
    module_collection_mode={"pptx": "py", "docx": "py", "pdf2docx": "py"},
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PDF Studio",
    icon="icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDF Studio")
