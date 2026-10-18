"""Render a verified physical PDF page; native viewer fragments are unreliable."""

from functools import lru_cache
import hashlib
from pathlib import Path


@lru_cache(maxsize=64)
def render_verified_page(path: Path, page_number: int, expected_sha256: str) -> bytes:
    import fitz

    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("PDF checksum changed before page rendering")
    with fitz.open(stream=content, filetype="pdf") as document:
        if not 1 <= page_number <= len(document):
            raise ValueError("Physical PDF page is out of bounds")
        image = document[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return image.tobytes("png")
