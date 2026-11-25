"""Text aus hochgeladenen Dateien gewinnen, damit er direkt in den Chat-Prompt eingebettet werden kann.

Copilot Chat kann keine lokalen Dateien lesen – deshalb landet der Inhalt als Text im Prompt.
Unterstützt: Excel (xlsx/xlsm), PDF, Word (docx) und alle Textformate (txt, md, csv, json, xml …).
Bilder und andere Binärdateien sind nicht einbettbar und müssen im Chat manuell angehängt werden.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

MAX_ROWS = 500
MAX_COLS = 40
MAX_PDF_PAGES = 60
SNIFF_BYTES = 8192


def extract_text(path: Path) -> str | None:
    """Liefert den Textinhalt oder ``None``, wenn die Datei nicht als Text einbettbar ist."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _excel(path)
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    return _plain_text(path)


def _plain_text(path: Path) -> str | None:
    raw = path.read_bytes()
    if b"\x00" in raw[:SNIFF_BYTES]:
        return None  # Binärdatei
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _excel(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            rows: list[list[str] | None] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS:
                    rows.append(None)
                    break
                cells = [_cell(v) for v in row[:MAX_COLS]]
                if any(cells):
                    rows.append(cells)
            parts.append(f"## Blatt: {ws.title}\n")
            data = [r for r in rows if r is not None]
            if not data:
                parts.append("_(leer)_\n")
                continue
            width = max(len(r) for r in data)
            for n, r in enumerate(data):
                r = r + [""] * (width - len(r))
                parts.append("| " + " | ".join(r) + " |")
                if n == 0:
                    parts.append("|" + "---|" * width)
            if None in rows:
                parts.append(f"\n_… gekürzt nach {MAX_ROWS} Zeilen_")
            parts.append("")
    finally:
        wb.close()
    return "\n".join(parts)


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PDF_PAGES:
            parts.append(f"_… gekürzt nach {MAX_PDF_PAGES} Seiten_")
            break
        text = (page.extract_text() or "").strip()
        parts.append(f"## Seite {i + 1}\n\n{text or '_(kein Text erkennbar – evtl. gescannt)_'}\n")
    return "\n".join(parts)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
