"""Original image fidelity, page ordering and bounded raster PDF wrapping."""
import builtins
import io

from PIL import Image, ImageOps
import pytest

from src.services import document_images as images
from src.services.situation_notices import documents


def image_bytes(*, size=(30, 20), format="PNG", frames=1, orientation=None):
    output = io.BytesIO()
    with Image.new("RGB", size, "red") as image:
        # Nonuniform pixels expose vertical flips, color swaps and resampling.
        image.putpixel((0, 0), (0, 255, 0))
        image.putpixel((size[0] - 1, size[1] - 1), (0, 0, 255))
        options = {}
        extras = []
        if frames > 1:
            extras = [Image.new("RGB", size, color) for color in ("blue", "green")[:frames - 1]]
            options.update(save_all=True, append_images=extras)
        if orientation is not None:
            exif = Image.Exif()
            exif[274] = orientation
            options["exif"] = exif
        try:
            image.save(output, format=format, **options)
        finally:
            for extra in extras:
                extra.close()
    return output.getvalue()


def raster_pages(data):
    """Copy PDF page pixels and extracted text before native objects close."""
    import pypdfium2 as pdfium

    result = []
    with images.PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_range()
                    finally:
                        text_page.close()
                    bitmap = page.render(scale=1)
                    try:
                        raster = bitmap.to_pil().convert("RGB")
                        try:
                            result.append({"size": raster.size, "pixels": raster.tobytes(), "text": text})
                        finally:
                            raster.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            pdf.close()
    return result


@pytest.mark.parametrize("format", ["PNG", "JPEG", "BMP", "WEBP"])
def test_original_pixels_are_preserved_without_a_text_layer(format):
    data = image_bytes(format=format)
    output = images.image_to_pdf(data)
    assert output.startswith(b"%PDF")
    pages = raster_pages(output)
    with Image.open(io.BytesIO(data)) as source:
        expected = source.convert("RGB")
        try:
            assert pages == [{"size": expected.size, "pixels": expected.tobytes(), "text": ""}]
        finally:
            expected.close()


def test_exif_rotation_is_applied_to_dimensions_and_pixels():
    data = image_bytes(format="JPEG", orientation=6)
    pages = raster_pages(images.image_to_pdf(data))
    with Image.open(io.BytesIO(data)) as source:
        oriented = ImageOps.exif_transpose(source)
        expected = oriented.convert("RGB")
        try:
            assert pages == [{"size": (20, 30), "pixels": expected.tobytes(), "text": ""}]
        finally:
            expected.close()
            oriented.close()


def test_transparent_pixels_use_a_white_page_background():
    output = io.BytesIO()
    with Image.new("RGBA", (30, 20), (255, 0, 0, 0)) as source:
        source.putpixel((0, 0), (0, 0, 255, 255))
        source.save(output, format="PNG")
    page = raster_pages(images.image_to_pdf(output.getvalue()))[0]
    expected = Image.new("RGB", (30, 20), "white")
    try:
        expected.putpixel((0, 0), (0, 0, 255))
        assert page["pixels"] == expected.tobytes()
        assert page["text"] == ""
    finally:
        expected.close()


def test_multiframe_images_keep_every_frame_in_original_order():
    data = image_bytes(format="TIFF", frames=3)
    pages = raster_pages(images.image_to_pdf(data))
    assert len(pages) == 3
    with Image.open(io.BytesIO(data)) as source:
        for index, page in enumerate(pages):
            source.seek(index)
            expected = source.convert("RGB")
            try:
                assert page == {"size": expected.size, "pixels": expected.tobytes(), "text": ""}
            finally:
                expected.close()


@pytest.mark.parametrize("data", [b"", b"not an image", b"%PDF-1.7", b"<svg></svg>", None, "image"])
def test_invalid_inputs_fail_explicitly(data):
    with pytest.raises(images.ImageDocumentError):
        images.image_to_pdf(data)


def test_input_byte_limit_rejects_oversized_original(monkeypatch):
    data = image_bytes()
    monkeypatch.setattr(images, "MAX_DOCUMENT_BYTES", len(data) - 1)
    with pytest.raises(images.ImageLimitError):
        images.image_to_pdf(data)


def test_frame_limit_does_not_silently_drop_extra_pages(monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_PAGES", 1)
    with pytest.raises(images.ImageLimitError):
        images.image_to_pdf(image_bytes(format="TIFF", frames=2))


def test_single_page_pixel_limit_rejects_oversized_raster(monkeypatch):
    monkeypatch.setattr(images, "MAX_PAGE_PIXELS", 599)
    with pytest.raises(images.ImageLimitError):
        images.image_to_pdf(image_bytes(size=(30, 20)))


def test_total_pixel_limit_accounts_for_all_frames(monkeypatch):
    monkeypatch.setattr(images, "MAX_TOTAL_PIXELS", 1199)
    with pytest.raises(images.ImageLimitError):
        images.image_to_pdf(image_bytes(format="TIFF", frames=2))


def test_output_byte_limit_rejects_oversized_pdf(monkeypatch):
    data = image_bytes()
    assert len(images.image_to_pdf(data)) > len(data)
    monkeypatch.setattr(images, "MAX_DOCUMENT_BYTES", len(data))
    with pytest.raises(images.ImageLimitError):
        images.image_to_pdf(data)


def test_wrapper_shares_the_existing_document_pdfium_lock():
    assert images.PDFIUM_LOCK is documents._PDFIUM_LOCK
    assert issubclass(images.ImageLimitError, images.ImageDocumentError)
    assert issubclass(images.ImageDocumentError, ValueError)


def test_wrapping_does_not_import_a_recognition_engine(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.partition(".")[0] in {"rapidocr", "onnxruntime"}:
            raise AssertionError("raster wrapping must not load a recognition engine")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert images.image_to_pdf(image_bytes()).startswith(b"%PDF")
