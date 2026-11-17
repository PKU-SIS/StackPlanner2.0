"""Real synthetic hybrid-xref PDFs: preserve pages/text without repeated traversal."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


@pytest.fixture
def hybrid_pdf(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 260 Td (Original evidence 123) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Generic original"})
    buffer = io.BytesIO()
    writer.write(buffer)
    base = buffer.getvalue()
    reader = PdfReader(io.BytesIO(base))
    root = reader.trailer.raw_get("/Root")
    size = reader.trailer["/Size"]
    import re

    previous = int(re.findall(rb"startxref\s+(\d+)", base)[-1])
    data = bytearray(base)
    depth = 8
    for _ in range(depth):
        stream_offset = len(data)
        data.extend(f"{size} 0 obj\n<< /Type /XRef /Size {size + 1} /W [1 4 2] /Index [0 1] /Length 7 /Prev {previous} >>\nstream\n".encode())
        data.extend(b"\x00\x00\x00\x00\x00\xff\xff\nendstream\nendobj\n")
        offset = len(data)
        data.extend(f"xref\n{size} 1\n{stream_offset:010d} 00000 n \ntrailer\n<< /Size {size + 1} /Root {root.idnum} {root.generation} R /Prev {previous} /XRefStm {stream_offset} >>\nstartxref\n{offset}\n%%EOF\n".encode())
        previous = offset
        size += 1
    source = tmp_path / "hybrid.pdf"
    source.write_bytes(data)
    return source, base, 2 * depth + 1


def test_each_unique_xref_once_preserves_text_and_does_not_patch_library(hybrid_pdf):
    from bid_audit.pdf_access import open_pdf
    from pdfminer.pdfdocument import PDFDocument

    source, base, expected = hybrid_pdf
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    original = PDFDocument.read_xref_from
    with open_pdf(source) as pdf:
        assert len(pdf.doc.xrefs) == expected
        assert len(pdf.pages) == 1
        assert pdf.pages[0].extract_text() == "Original evidence 123"
        stream = pdf.stream
    assert stream.closed
    assert PDFDocument.read_xref_from is original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_documents_have_independent_visited_sets_and_external_stream_ownership(hybrid_pdf):
    from bid_audit.pdf_access import open_pdf

    source, _, expected = hybrid_pdf

    def read():
        stream = io.BytesIO(source.read_bytes())
        with open_pdf(stream, unicode_norm="NFKC") as pdf:
            result = (len(pdf.doc.xrefs), pdf.pages[0].extract_text(), pdf.unicode_norm)
        assert not stream.closed
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert results == [(expected, "Original evidence 123", "NFKC")] * 2


def test_invalid_original_stays_an_error(tmp_path):
    from bid_audit.pdf_access import open_pdf

    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"not a valid PDF")
    with pytest.raises(Exception):
        with open_pdf(source):
            pass
    assert source.read_bytes() == b"not a valid PDF"
