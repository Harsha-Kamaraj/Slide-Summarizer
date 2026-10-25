"""Render slides as PNG images, for the vision model.

PDFs are rendered directly with PyMuPDF. PowerPoint files are first converted
to PDF with LibreOffice (python-pptx can read text but can't draw slides).
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from backend.extract import ExtractionError

logger = logging.getLogger(__name__)

# About 1024 px wide: enough for the model to read small slide text, while
# keeping each image (and the tokens it costs) reasonably small.
TARGET_WIDTH_PX = 1024


def render_slides(path: str | Path, numbers: list[int]) -> dict[int, bytes]:
    """Return {slide number: PNG bytes} for the requested 1-based slide numbers."""
    path = Path(path)
    if not numbers:
        return {}
    if path.suffix.lower() == ".pdf":
        return _render_pdf(path, numbers)
    with tempfile.TemporaryDirectory() as out_dir:
        return _render_pdf(_convert_to_pdf(path, Path(out_dir)), numbers)


def _render_pdf(path: Path, numbers: list[int]) -> dict[int, bytes]:
    images = {}
    with pymupdf.open(path) as doc:
        for number in numbers:
            if not 1 <= number <= len(doc):
                continue
            page = doc[number - 1]
            zoom = TARGET_WIDTH_PX / page.rect.width
            images[number] = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    logger.info("Rendered %d slide images", len(images))
    return images


def _convert_to_pdf(path: Path, out_dir: Path) -> Path:
    soffice = shutil.which("soffice")
    if soffice is None:
        raise ExtractionError("Rendering PowerPoint slides needs LibreOffice installed.")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError(f"Could not render the slides of '{path.name}'.") from exc
    converted = out_dir / f"{path.stem}.pdf"
    if not converted.exists():
        raise ExtractionError(f"Could not render the slides of '{path.name}'.")
    return converted
