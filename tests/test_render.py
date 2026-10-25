import shutil

import pytest

from backend.render import TARGET_WIDTH_PX, render_slides

PNG_MAGIC = b"\x89PNG"


def test_renders_requested_pdf_pages(sample_pdf):
    images = render_slides(sample_pdf, [2])
    assert list(images) == [2]
    assert images[2].startswith(PNG_MAGIC)


def test_ignores_out_of_range_and_empty_requests(sample_pdf):
    assert render_slides(sample_pdf, []) == {}
    assert render_slides(sample_pdf, [99]) == {}


def test_image_width_targets_vision_size(sample_pdf):
    import pymupdf

    png = render_slides(sample_pdf, [1])[1]
    assert pymupdf.Pixmap(png).width == TARGET_WIDTH_PX


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_renders_pptx_via_libreoffice(sample_pptx):
    images = render_slides(sample_pptx, [1, 3])
    assert sorted(images) == [1, 3]
    assert all(png.startswith(PNG_MAGIC) for png in images.values())
