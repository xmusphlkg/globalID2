"""Bounded original-image PDFs for independent visual model review.

The wrapper preserves orientation-corrected source pixels and adds no text.
Each image frame becomes one PDF page, without recognition or inferred values.
"""
from __future__ import annotations

import io
from typing import Any, Iterator

from src.services.situation_notices.documents import _PDFIUM_LOCK as PDFIUM_LOCK

MAX_DOCUMENT_BYTES = 40 * 1024 * 1024
MAX_IMAGE_PAGES = 40
MAX_PAGE_PIXELS = 24_000_000
MAX_TOTAL_PIXELS = 100_000_000
SUPPORTED_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "TIFF", "GIF", "WEBP", "BMP"})


class ImageDocumentError(ValueError):
    """The supplied bytes are not a supported image document."""


class ImageLimitError(ImageDocumentError):
    """The image document exceeds a configured resource bound."""


def _validate_input(data: bytes) -> None:
    if not isinstance(data, bytes) or not data:
        raise ImageDocumentError("document must be nonempty bytes")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ImageLimitError(f"document exceeds the {MAX_DOCUMENT_BYTES}-byte limit")


def _check_pixels(width: int, height: int, total: int) -> int:
    pixels = width * height
    if width < 1 or height < 1 or pixels > MAX_PAGE_PIXELS:
        raise ImageLimitError(f"page dimensions {width}x{height} exceed the {MAX_PAGE_PIXELS}-pixel limit")
    total += pixels
    if total > MAX_TOTAL_PIXELS:
        raise ImageLimitError(f"document exceeds the {MAX_TOTAL_PIXELS}-pixel total limit")
    return total


def _image_pages(data: bytes) -> Iterator[Any]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        source = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageDocumentError("document is not a supported image") from exc
    with source:
        if source.format not in SUPPORTED_IMAGE_FORMATS:
            raise ImageDocumentError(f"unsupported image format: {source.format}")
        count = getattr(source, "n_frames", 1)
        if count > MAX_IMAGE_PAGES:
            raise ImageLimitError(f"image has {count} frames, over the {MAX_IMAGE_PAGES}-page limit")
        # Preflight every frame before converting any page to a PDF raster.
        total = 0
        for index in range(count):
            source.seek(index)
            total = _check_pixels(*source.size, total)
        for index in range(count):
            source.seek(index)
            oriented = ImageOps.exif_transpose(source)
            try:
                if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    try:
                        image = Image.new("RGB", rgba.size, "white")
                        image.paste(rgba, mask=rgba.getchannel("A"))
                    finally:
                        rgba.close()
                else:
                    image = oriented.convert("RGB")
            finally:
                if oriented is not source:
                    oriented.close()
            try:
                yield image
            finally:
                image.close()


def image_to_pdf(data: bytes) -> bytes:
    """Wrap each original image frame in a losslessly compressed, text-free PDF.

    All PDFium calls share the existing document lock. Inputs are rejected on
    byte, page or pixel limits; no frame is silently omitted or downsampled.
    """
    import pypdfium2 as pdfium

    _validate_input(data)
    iterator = _image_pages(data)
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument.new()
        try:
            for image in iterator:
                page = pdf.new_page(image.width, image.height)
                bitmap = None
                image_object = None
                inserted = False
                try:
                    bitmap = pdfium.PdfBitmap.from_pil(image)
                    image_object = pdfium.PdfImage.new(pdf)
                    image_object.set_bitmap(bitmap)
                    image_object.set_matrix(pdfium.PdfMatrix(image.width, 0, 0, image.height, 0, 0))
                    page.insert_obj(image_object)
                    inserted = True
                    page.gen_content()
                finally:
                    if image_object is not None and not inserted:
                        image_object.close()
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
            buffer = io.BytesIO()
            pdf.save(buffer)
            result = buffer.getvalue()
            if len(result) > MAX_DOCUMENT_BYTES:
                raise ImageLimitError(f"raster PDF exceeds the {MAX_DOCUMENT_BYTES}-byte limit")
            return result
        finally:
            iterator.close()
            pdf.close()
