# PDF Studio

A Windows program to **view, edit, sign, protect and convert PDF files**.

## Download
Go to **Releases** (right side of this page) → download **PDF-Studio-Setup.exe** → double-click to install.

## Features
- **View**: thumbnails, zoom, search, print, password-protected files
- **Edit**: change existing text, add text, highlight/underline/strike, boxes, circles, lines, arrows, freehand pen, sticky notes, images, signature, whiteout, permanent redaction, undo/redo
- **Pages**: reorder by drag & drop, rotate, delete, duplicate, insert blank/from file, extract, crop
- **Document**: merge PDFs & images, split, watermark, page numbers, header/footer, password protection (AES-256), compress, flatten, properties
- **Export**: Word, Excel (tables), PowerPoint, PNG, JPG, TIFF, SVG, CSV, Text, HTML, Markdown, RTF, JSON, compressed PDF, embedded images

## How it is built
Every push to `main` makes GitHub build `PDF-Studio-Setup.exe`, run a self-test of all export formats, and publish a new Release automatically (see `.github/workflows/build.yml`).

Built with Python, PySide6 (Qt) and PyMuPDF.
