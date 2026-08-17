"""Tests for document text extraction (PDF/DOCX/PPTX/XLSX/CSV/MD)."""

from __future__ import annotations

import io
import zipfile

import pytest

from agent.services import doc_extract


def _minimal_docx(paragraphs: list[str]) -> bytes:
    """Build a tiny valid DOCX (OOXML zip) for unit tests."""
    body_runs = []
    for p in paragraphs:
        body_runs.append(
            f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(p)}</w:t></w:r></w:p>'
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body_runs)}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _minimal_pptx(slide_texts: list[str]) -> bytes:
    slides_xml = []
    for i, text in enumerate(slide_texts, start=1):
        slides_xml.append(
            (
                i,
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{_xml_escape(text)}"
                "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
            )
        )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        for i, xml in slides_xml:
            zf.writestr(f"ppt/slides/slide{i}.xml", xml)
    return buf.getvalue()


def _minimal_xlsx() -> bytes:
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">'
        "<si><t>Name</t></si><si><t>Alice</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="People" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
    return buf.getvalue()


def test_docx_extracts_paragraphs_not_zip_bytes():
    raw = _minimal_docx(["QA sign-off is required", "Deploy after approval"])
    # Sanity: raw is a ZIP — must NOT be ingested as utf-8 text
    assert raw[:2] == b"PK"
    text, status = doc_extract.extract_bytes(raw, filename="policy.docx")
    assert status == "docx_extracted"
    assert "QA sign-off is required" in text
    assert "Deploy after approval" in text
    assert "word/document.xml" not in text
    assert not doc_extract.looks_like_binary_garbage(text)


def test_pptx_extracts_slides():
    raw = _minimal_pptx(["Release checklist", "Rollback plan"])
    text, status = doc_extract.extract_bytes(raw, filename="deck.pptx")
    assert status == "pptx_extracted"
    assert "Release checklist" in text
    assert "Slide" in text


def test_xlsx_extracts_cells():
    raw = _minimal_xlsx()
    text, status = doc_extract.extract_bytes(raw, filename="roster.xlsx")
    assert status == "xlsx_extracted"
    assert "Name" in text
    assert "Alice" in text


def test_csv_and_markdown():
    csv_bytes = b"name,role\nSunny,Architect\n"
    text, status = doc_extract.extract_bytes(csv_bytes, filename="team.csv")
    assert status == "csv_extracted"
    assert "Sunny" in text

    md = b"# Policy\n\nRequire **QA** before prod.\n"
    text2, status2 = doc_extract.extract_bytes(md, filename="policy.md")
    assert status2 == "markdown_extracted"
    assert "QA" in text2


def test_pdf_extract(monkeypatch):
    class _Page:
        def extract_text(self):
            return "PDF body: release gate"

    class _Reader:
        def __init__(self, _buf):
            self.pages = [_Page()]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    text, status = doc_extract.extract_bytes(b"%PDF-1.4 fake", filename="a.pdf")
    assert status == "pdf_extracted"
    assert "release gate" in text


def test_rejects_raw_zip_as_text_garbage():
    # Simulate old bug: decoding DOCX as utf-8
    raw = _minimal_docx(["secret policy text"])
    mojibake = raw.decode("utf-8", errors="replace")
    assert doc_extract.looks_like_binary_garbage(mojibake)


def test_legacy_ole_graceful():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20
    text, status = doc_extract.extract_bytes(ole, filename="old.doc")
    assert status == "unsupported_legacy_ole"
    assert "docx" in text.lower()


def test_detect_format_by_magic():
    assert doc_extract.detect_format(b"%PDF-1.7\n", filename="x.bin") == "pdf"
    assert doc_extract.detect_format(_minimal_docx(["a"]), filename="x.bin") == "docx"
