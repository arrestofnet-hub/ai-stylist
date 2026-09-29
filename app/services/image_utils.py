from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def sanitize_image_bytes(
    content: bytes,
    *,
    output_format: str = "WEBP",
    quality: int = 95,
) -> tuple[bytes, str]:
    if not content:
        raise InvalidImageError("Image is empty")

    try:
        with Image.open(BytesIO(content)) as source:
            source.verify()

        with Image.open(BytesIO(content)) as source:
            source = ImageOps.exif_transpose(source)

            width, height = source.size
            if width < 256 or height < 256:
                raise InvalidImageError("Image is too small; minimum is 256x256")
            if width * height > MAX_IMAGE_PIXELS:
                raise InvalidImageError("Image dimensions are too large")

            if source.mode not in {"RGB", "RGBA"}:
                source = source.convert("RGB")

            if output_format.upper() == "JPEG" and source.mode == "RGBA":
                background = Image.new("RGB", source.size, "white")
                background.paste(source, mask=source.getchannel("A"))
                source = background

            out = BytesIO()
            save_kwargs: dict[str, object] = {}

            normalized = output_format.upper()
            if normalized == "WEBP":
                save_kwargs = {"quality": quality, "method": 6}
                mime_type = "image/webp"
            elif normalized == "JPEG":
                save_kwargs = {"quality": quality, "optimize": True}
                mime_type = "image/jpeg"
            elif normalized == "PNG":
                save_kwargs = {"optimize": True}
                mime_type = "image/png"
            else:
                raise InvalidImageError("Unsupported output image format")

            source.save(out, format=normalized, **save_kwargs)
            return out.getvalue(), mime_type

    except InvalidImageError:
        raise
    except Image.DecompressionBombError as exc:
        raise InvalidImageError("Image dimensions are unsafe") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("Uploaded file is not a valid image") from exc


def sanitize_image_file(
    source_path: Path,
    destination_path: Path,
    *,
    output_format: str = "WEBP",
    quality: int = 95,
) -> tuple[int, str]:
    content = source_path.read_bytes()
    sanitized, mime_type = sanitize_image_bytes(
        content,
        output_format=output_format,
        quality=quality,
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(sanitized)
    return len(sanitized), mime_type
