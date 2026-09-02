"""Requirement-document text extraction (real file intake).

The intake write keeps taking plain ``requirement_text``; this is the parser
that turns an uploaded document into that text. Unit tests cover every
supported format with real in-memory files; the HTTP test covers auth, the
multipart round trip, and the refusal paths (unsupported / oversized / no
extractable text).
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.repository_intelligence.application.document_parse import (
    MAX_DOCUMENT_BYTES,
    UnsupportedDocumentFormat,
    extract_document_text,
)
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


def _docx_bytes(text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _pdf_bytes(text: str) -> bytes:
    """A minimal but structurally valid single-page PDF (with xref table)."""

    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode())
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\n"
    )
    out.write(f"startxref\n{xref_pos}\n%%EOF\n".encode())
    return out.getvalue()


def _odt_bytes(text: str) -> bytes:
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<office:body><office:text>"
        f"<text:p>{text}</text:p>"
        "</office:text></office:body></office:document-content>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.xml", content_xml)
    return buffer.getvalue()


def _rtf_bytes(text: str) -> bytes:
    escaped = "".join(ch if ord(ch) < 128 else f"\\u{ord(ch)}?" for ch in text)
    return (
        r"{\rtf1\ansi{\fonttbl{\f0\fnil Courier;}}\f0\pard " + escaped + r"\par}"
    ).encode("latin-1")


# ---------------------------------------------------------------------------
# Unit: extract_document_text across the supported formats
# ---------------------------------------------------------------------------


def test_extract_plain_text_and_markdown() -> None:
    parsed = extract_document_text("需求.md", "## 目标\n\n支持上传".encode())
    assert parsed.format == "markdown"
    assert "目标" in parsed.text
    assert parsed.chars == len(parsed.text)
    assert not parsed.truncated


def test_extract_docx() -> None:
    parsed = extract_document_text("PRD.docx", _docx_bytes("结算页支持满额免运费"))
    assert parsed.format == "docx"
    assert "免运费" in parsed.text


def test_extract_pdf() -> None:
    parsed = extract_document_text("spec.pdf", _pdf_bytes("Hello PDF"))
    assert parsed.format == "pdf"
    assert "Hello PDF" in parsed.text


def test_extract_odt() -> None:
    parsed = extract_document_text("req.odt", _odt_bytes("需求来自 odt"))
    assert parsed.format == "odt"
    assert "odt" in parsed.text


def test_extract_rtf() -> None:
    parsed = extract_document_text("req.rtf", _rtf_bytes("需求来自 rtf"))
    assert parsed.format == "rtf"
    assert "rtf" in parsed.text


def test_unsupported_format_refused() -> None:
    with pytest.raises(UnsupportedDocumentFormat):
        extract_document_text("image.png", b"\x89PNG\r\n\x1a\n")
    with pytest.raises(UnsupportedDocumentFormat):
        extract_document_text("notes", b"no extension")


def test_no_extractable_text_refused() -> None:
    with pytest.raises(Exception) as excinfo:
        extract_document_text("blank.txt", b"   \n\t ")
    assert "no extractable text" in str(excinfo.value)


def test_oversized_refused() -> None:
    with pytest.raises(Exception) as excinfo:
        extract_document_text("big.txt", b"x" * (MAX_DOCUMENT_BYTES + 1))
    assert "too large" in str(excinfo.value)


def test_truncation_to_intake_limit() -> None:
    from repomesh.modules.repository_intelligence.application.document_parse import (
        MAX_INTAKE_TEXT_CHARS,
    )

    parsed = extract_document_text("long.txt", b"y" * (MAX_INTAKE_TEXT_CHARS + 500))
    assert parsed.truncated
    assert parsed.chars == MAX_INTAKE_TEXT_CHARS


# ---------------------------------------------------------------------------
# HTTP: POST /api/v1/issues/parse-document
# ---------------------------------------------------------------------------


def test_parse_document_over_http(
    application_container: ApplicationContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    with TestClient(create_app(application_container)) as client:
        # Auth is required, like every write on this router.
        response = client.post(
            "/api/v1/issues/parse-document",
            files={"file": ("req.txt", "需求文本".encode(), "text/plain")},
        )
        assert response.status_code == 401

        # Happy path: markdown document → extracted text.
        response = client.post(
            "/api/v1/issues/parse-document",
            files={"file": ("req.md", "# 需求\n\n支持真实上传".encode(), "text/markdown")},
            headers=HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["filename"] == "req.md"
        assert body["format"] == "markdown"
        assert "真实上传" in body["text"]
        assert body["chars"] == len(body["text"])
        assert body["truncated"] is False

        # Unsupported format → 415.
        response = client.post(
            "/api/v1/issues/parse-document",
            files={"file": ("img.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            headers=HEADERS,
        )
        assert response.status_code == 415

        # Readable file with no text → 422.
        response = client.post(
            "/api/v1/issues/parse-document",
            files={"file": ("blank.md", b"   ", "text/markdown")},
            headers=HEADERS,
        )
        assert response.status_code == 422
