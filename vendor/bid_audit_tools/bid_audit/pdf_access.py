"""Read original PDFs with per-document hybrid-xref de-duplication.

No installed module, original PDF bytes, page selection, or parsing strategy is
changed. The pdfplumber constructor is mirrored only to inject PDFDocument's
scoped subclass; all page/table/text implementations remain upstream.
"""

from pdfminer.pdfdocument import PDFDocument
from pdfplumber.pdf import (
    PDF,
    LAParams,
    PdfminerException,
    PDFParser,
    PDFResourceManager,
    logger,
    resolve_and_decode,
)

PDF_ACCESS_VERSION = "visited-xref-1"


class XRefDocument(PDFDocument):
    def __init__(self, *args, **kwargs):
        self._visited_offsets = set()
        super().__init__(*args, **kwargs)

    def read_xref_from(self, parser, start, xrefs):
        if start in self._visited_offsets:
            return None
        self._visited_offsets.add(start)
        try:
            return super().read_xref_from(parser, start, xrefs)
        except BaseException:
            self._visited_offsets.discard(start)
            raise


class OriginalPDF(PDF):
    def __init__(
        self,
        stream,
        stream_is_external=False,
        path=None,
        pages=None,
        laparams=None,
        password=None,
        strict_metadata=False,
        unicode_norm=None,
        raise_unicode_errors=True,
    ):
        self.stream = stream
        self.stream_is_external = stream_is_external
        self.path = path
        self.pages_to_parse = pages
        self.laparams = None if laparams is None else LAParams(**laparams)
        self.password = password
        self.unicode_norm = unicode_norm
        self.raise_unicode_errors = raise_unicode_errors
        try:
            self.doc = XRefDocument(PDFParser(stream), password=password or "")
        except Exception as exc:
            raise PdfminerException(exc) from exc
        self.rsrcmgr = PDFResourceManager()
        self.metadata = {}
        for info in self.doc.info:
            self.metadata.update(info)
        for key, value in self.metadata.items():
            try:
                self.metadata[key] = resolve_and_decode(value)
            except Exception as exc:
                if strict_metadata:
                    raise
                logger.warning("Metadata key %s could not be parsed: %s", key, exc)


def open_pdf(path, **kwargs):
    if kwargs.get("repair"):
        raise ValueError("Audit originals must not be rewritten by repair")
    return OriginalPDF.open(path, **kwargs)
