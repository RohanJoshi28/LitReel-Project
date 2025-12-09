from litreel.services.pdf_parser import (
    extract_text_from_document,
    extract_text_from_pdf,
)


def test_extract_text_from_pdf(sample_pdf):
    text = extract_text_from_document(sample_pdf)
    assert "viral-ready nonfiction" in text


def test_extract_text_from_docx(sample_docx):
    text = extract_text_from_document(sample_docx)
    assert "viral-ready nonfiction" in text


def test_extract_text_from_epub(sample_epub):
    text = extract_text_from_document(sample_epub)
    assert "viral-ready nonfiction" in text


def test_extract_text_from_pdf_alias(sample_pdf):
    via_pdf = extract_text_from_pdf(sample_pdf)
    via_generic = extract_text_from_document(sample_pdf)
    assert via_pdf == via_generic
