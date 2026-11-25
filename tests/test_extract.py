import zipfile
from pathlib import Path

from pager.core.extract import extract_text


def test_plain_text_and_encodings(tmp_path: Path):
    (tmp_path / "a.csv").write_text("a;b\n1;2", encoding="utf-8")
    (tmp_path / "b.txt").write_bytes("Größe".encode("cp1252"))
    (tmp_path / "c.bin").write_bytes(b"\x00\x01\x02")
    assert extract_text(tmp_path / "a.csv") == "a;b\n1;2"
    assert extract_text(tmp_path / "b.txt") == "Größe"
    assert extract_text(tmp_path / "c.bin") is None


def test_docx(tmp_path: Path):
    path = tmp_path / "brief.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml",
                    '<w:document><w:body><w:p><w:r><w:t>Hallo &amp; Willkommen</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>Zeile</w:t><w:tab/><w:t>zwei</w:t></w:r></w:p></w:body></w:document>')
    assert extract_text(path) == "Hallo & Willkommen\nZeile\tzwei"
