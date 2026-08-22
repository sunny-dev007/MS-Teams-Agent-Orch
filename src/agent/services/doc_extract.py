"""Document text extraction for Document Knowledge Fabric.

Supports: PDF, DOCX, PPTX, XLSX, CSV, MD/TXT/HTML/JSON and related text types.
OOXML (docx/pptx/xlsx) uses stdlib zipfile + XML — no heavy Office SDK.
PDF uses pypdf (lightweight). Legacy binary OLE (.doc/.ppt/.xls) returns a
graceful unsupported message rather than ingesting mojibake into Qdrant.

Feature: Document Knowledge Fabric — isolated from Dev/WhatsApp coding paths.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from agent.core.logging import get_logger

logger = get_logger(__name__)

# OOXML / Open Packaging Conventions
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_OOXML_EXTS = {".docx", ".pptx", ".xlsx"}
_PDF_EXTS = {".pdf"}
_VTT_EXTS = {".vtt"}
_CSV_EXTS = {".csv", ".tsv"}
_TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yml",
    ".yaml",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".cs",
    ".java",
    ".css",
    ".sql",
}
_LEGACY_OLE = {".doc", ".ppt", ".xls"}  # need LibreOffice/antiword — not on App Service


def detect_format(data: bytes, *, filename: str = "", mime: str = "") -> str:
    """Return canonical format key from magic bytes + filename/mime hints."""
    name = (filename or "").lower()
    mime_l = (mime or "").lower()
    ext = ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1]

    head = data[:8] if data else b""
    # PDF
    if head.startswith(b"%PDF") or ext in _PDF_EXTS or "pdf" in mime_l:
        return "pdf"
    # OOXML is a ZIP
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        kind = _ooxml_kind(data)
        if kind:
            return kind
        if ext in _OOXML_EXTS:
            return ext.lstrip(".")
        if "wordprocessingml" in mime_l or ext == ".docx":
            return "docx"
        if "presentationml" in mime_l or ext == ".pptx":
            return "pptx"
        if "spreadsheetml" in mime_l or ext == ".xlsx":
            return "xlsx"
        return "zip_unknown"
    # Legacy OLE Compound File
    if head.startswith(b"\xd0\xcf\x11\xe0") or ext in _LEGACY_OLE:
        return "legacy_ole"
    if ext in _CSV_EXTS or "csv" in mime_l or "tab-separated" in mime_l:
        return "csv"
    if ext in _VTT_EXTS or "vtt" in mime_l or (data[:15].decode("utf-8", errors="ignore").upper().startswith("WEBVTT")):
        return "vtt"
    if ext in {".md", ".markdown"} or "markdown" in mime_l:
        return "markdown"
    if ext in {".html", ".htm"} or "html" in mime_l:
        return "html"
    if ext in _TEXT_EXTS or mime_l.startswith("text/") or "json" in mime_l or "xml" in mime_l:
        return "text"
    if ext == ".docx":
        return "docx"
    if ext == ".pptx":
        return "pptx"
    if ext == ".xlsx":
        return "xlsx"
    if ext == ".pdf":
        return "pdf"
    return "unknown"


def _ooxml_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" in names:
                return "docx"
            if any(n.startswith("ppt/slides/slide") for n in names):
                return "pptx"
            if "xl/workbook.xml" in names or any(n.startswith("xl/worksheets/") for n in names):
                return "xlsx"
    except zipfile.BadZipFile:
        return None
    return None


def _xml_local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def extract_docx(data: bytes) -> str:
    """Extract paragraph text from DOCX (word/document.xml)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for p in root.iter(f"{_W_NS}p"):
        parts: list[str] = []
        for node in p.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                parts.append("\t")
            elif node.tag == f"{_W_NS}br":
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs).strip()


def extract_pptx(data: bytes) -> str:
    """Extract text from PPTX slides in order."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slide_names = sorted(
            n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
        )
        blocks: list[str] = []
        for i, name in enumerate(slide_names, start=1):
            root = ET.fromstring(zf.read(name))
            texts: list[str] = []
            for node in root.iter():
                if node.tag == f"{_A_NS}t" and node.text and node.text.strip():
                    texts.append(node.text.strip())
            if texts:
                blocks.append(f"## Slide {i}\n" + "\n".join(texts))
    return "\n\n".join(blocks).strip()


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.iter(f"{_S_NS}si"):
        parts = [t.text or "" for t in si.iter(f"{_S_NS}t")]
        strings.append("".join(parts))
    return strings


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    v = cell.find(f"{_S_NS}v")
    if v is None or v.text is None:
        # inline string
        is_node = cell.find(f"{_S_NS}is")
        if is_node is not None:
            return "".join(t.text or "" for t in is_node.iter(f"{_S_NS}t"))
        return ""
    raw = v.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def extract_xlsx(data: bytes, *, max_rows_per_sheet: int = 500, max_cols: int = 40) -> str:
    """Extract tabular text from XLSX sheets (sharedStrings + cells)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        # Sheet order from workbook
        sheet_files: list[tuple[str, str]] = []
        if "xl/workbook.xml" in zf.namelist():
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            rels: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in zf.namelist():
                rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in rel_root:
                    rid = rel.attrib.get("Id")
                    target = rel.attrib.get("Target") or ""
                    if rid and target:
                        if not target.startswith("xl/"):
                            target = "xl/" + target.lstrip("/")
                        rels[rid] = target
            for sh in wb.iter(f"{_S_NS}sheet"):
                title = sh.attrib.get("name") or "Sheet"
                rid = sh.attrib.get(f"{_R_NS}id") or sh.attrib.get("id")
                path = rels.get(rid or "", "")
                if path:
                    sheet_files.append((title, path))
        if not sheet_files:
            sheet_files = [
                (f"Sheet{i}", n)
                for i, n in enumerate(
                    sorted(x for x in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", x)),
                    start=1,
                )
            ]

        blocks: list[str] = []
        for title, path in sheet_files:
            if path not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(path))
            rows_out: list[str] = []
            for row in root.iter(f"{_S_NS}sheetData"):
                for r in row.findall(f"{_S_NS}row"):
                    cells = r.findall(f"{_S_NS}c")[:max_cols]
                    vals = [_xlsx_cell_value(c, shared).replace("\n", " ").strip() for c in cells]
                    if any(vals):
                        rows_out.append(" | ".join(vals))
                    if len(rows_out) >= max_rows_per_sheet:
                        break
            if rows_out:
                blocks.append(f"## Sheet: {title}\n" + "\n".join(rows_out))
    return "\n\n".join(blocks).strip()


def extract_pdf(data: bytes, *, max_pages: int = 100) -> str:
    """Extract text from PDF via pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pypdf is required for PDF extraction. Install project deps / redeploy."
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            logger.exception("PDF page %s extract failed", i + 1)
            continue
        if text:
            pages.append(f"## Page {i + 1}\n{text}")
    return "\n\n".join(pages).strip()


def extract_csv(data: bytes, *, max_rows: int = 1000) -> str:
    """Decode CSV/TSV with encoding fallbacks; render as markdown-ish rows."""
    text = _decode_text(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
        if sample.count("\t") > sample.count(","):
            dialect = csv.excel_tab
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[str] = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            rows.append(f"... truncated after {max_rows} rows")
            break
        cells = [c.replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows).strip()


def extract_vtt(data: bytes) -> str:
    from agent.services import vtt_parse

    cues = vtt_parse.parse_vtt(data)
    return vtt_parse.vtt_to_plain_text(cues)


def extract_text_plain(data: bytes) -> str:
    return _decode_text(data).strip()


def _html_to_text(html: str) -> str:
    """Minimal HTML → text (avoid importing graph_docs — circular risk)."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.chunks: list[str] = []
            self.skip = False

        def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
            if tag in ("script", "style"):
                self.skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self.skip = False

        def handle_data(self, data: str) -> None:
            if not self.skip and data and data.strip():
                self.chunks.append(data.strip())

    p = _P()
    try:
        p.feed(html or "")
        return "\n".join(p.chunks)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def looks_like_binary_garbage(text: str) -> bool:
    """Heuristic to reject mojibake / ZIP headers accidentally treated as text."""
    if not text:
        return True
    sample = text[:2000]
    if "PK\x03\x04" in sample or "word/document.xml" in sample or "word/theme/" in sample:
        return True
    if sample.lstrip().startswith("%PDF"):
        return True
    # High ratio of replacement / non-printable
    bad = sum(1 for ch in sample if ord(ch) < 9 or (13 < ord(ch) < 32) or ch == "\ufffd")
    return (bad / max(1, len(sample))) > 0.08


def extract_bytes(
    data: bytes,
    *,
    filename: str = "",
    mime: str = "",
) -> tuple[str, str]:
    """Extract plain text. Returns (text, extract_status).

    extract_status examples: docx_extracted, pdf_extracted, unsupported_legacy_ole, empty
    """
    if not data:
        return "", "empty"

    fmt = detect_format(data, filename=filename, mime=mime)
    try:
        if fmt == "docx":
            text = extract_docx(data)
            status = "docx_extracted"
        elif fmt == "pptx":
            text = extract_pptx(data)
            status = "pptx_extracted"
        elif fmt == "xlsx":
            text = extract_xlsx(data)
            status = "xlsx_extracted"
        elif fmt == "pdf":
            text = extract_pdf(data)
            status = "pdf_extracted"
        elif fmt == "csv":
            text = extract_csv(data)
            status = "csv_extracted"
        elif fmt == "vtt":
            text = extract_vtt(data)
            status = "vtt_extracted"
        elif fmt in ("markdown", "text", "html"):
            text = extract_text_plain(data)
            if fmt == "html":
                text = _html_to_text(text)
                status = "html_extracted"
            else:
                status = "markdown_extracted" if fmt == "markdown" else "text_extracted"
        elif fmt == "legacy_ole":
            return (
                f"Document: {filename or 'legacy Office file'}\n"
                "Format: legacy .doc/.ppt/.xls (OLE) is not text-extractable on App Service.\n"
                "Please re-save as .docx / .pptx / .xlsx / .pdf / .md and re-ingest.",
                "unsupported_legacy_ole",
            )
        elif fmt == "zip_unknown":
            return (
                f"Document: {filename or 'archive'}\n"
                "Unrecognized ZIP/Office package — could not extract text.",
                "unsupported_zip",
            )
        else:
            # Last resort: try OOXML/PDF magic again, else refuse binary
            if data[:4] == b"%PDF":
                text = extract_pdf(data)
                status = "pdf_extracted"
            elif data[:2] == b"PK":
                kind = _ooxml_kind(data)
                if kind == "docx":
                    text = extract_docx(data)
                    status = "docx_extracted"
                elif kind == "pptx":
                    text = extract_pptx(data)
                    status = "pptx_extracted"
                elif kind == "xlsx":
                    text = extract_xlsx(data)
                    status = "xlsx_extracted"
                else:
                    return (
                        f"Document: {filename or 'binary'}\n"
                        "Unsupported binary format for RAG extraction.",
                        "unsupported_binary",
                    )
            else:
                # Attempt text decode but reject garbage
                text = extract_text_plain(data)
                status = "text_extracted"
                if looks_like_binary_garbage(text):
                    return (
                        f"Document: {filename or 'file'}\n"
                        "Binary content detected — not ingested as text. "
                        "Use PDF/DOCX/PPTX/XLSX/CSV/MD.",
                        "rejected_binary",
                    )

        text = (text or "").strip()
        if not text:
            return "", f"{status}_empty"
        if looks_like_binary_garbage(text) and fmt not in ("markdown", "text", "csv", "html"):
            # Shouldn't happen after proper extractors, but guard Qdrant quality
            logger.warning("Extractor produced binary-looking text for %s (%s)", filename, fmt)
            return "", "rejected_binary_output"
        return text, status
    except Exception as exc:
        logger.exception("Extraction failed for %s format=%s", filename, fmt)
        return (
            f"Document: {filename or 'file'}\nExtraction failed ({fmt}): {exc}",
            f"extract_error_{fmt}",
        )


def is_supported_extension(ext: str, mime: str = "") -> bool:
    e = (ext or "").lower()
    m = (mime or "").lower()
    if e in _OOXML_EXTS | _PDF_EXTS | _CSV_EXTS | _TEXT_EXTS | {".md", ".markdown"}:
        return True
    if e in _LEGACY_OLE:
        return True  # handled with graceful message
    if "officedocument" in m or "pdf" in m or m.startswith("text/") or "csv" in m:
        return True
    return False
