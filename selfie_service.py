from pathlib import Path
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
import base64
import binascii
import secrets

PHOTO_DIR = (
    Path(__file__).resolve().parent
    / "private_data"
    / "attendance_photos"
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_OUTPUT_SIZE = (1280, 1280)


def save_selfie(image_data, action):
    """Validate and privately save a clock-in or clock-out selfie."""

    if action not in ("in", "out"):
        raise ValueError("Invalid attendance photo action.")

    if not isinstance(image_data, str) or not image_data:
        raise ValueError("Attendance selfie is required.")

    prefix = "data:image/jpeg;base64,"
    png_prefix = "data:image/png;base64,"

    if image_data.startswith(prefix):
        encoded = image_data[len(prefix):]
    elif image_data.startswith(png_prefix):
        encoded = image_data[len(png_prefix):]
    else:
        raise ValueError("Only JPEG or PNG camera images are accepted.")

    if len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 4:
        raise ValueError("Attendance selfie is too large.")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid attendance selfie encoding.")

    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Attendance selfie exceeds the size limit.")

    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in ("JPEG", "PNG"):
                raise ValueError("Invalid attendance image format.")

            width, height = image.size

            if (
                width < 160
                or height < 160
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Invalid attendance image dimensions.")

            image.load()
            processed = ImageOps.exif_transpose(image)
            processed.thumbnail(MAX_OUTPUT_SIZE)

            clean_image = Image.new("RGB", processed.size)
            clean_image.paste(processed.convert("RGB"))

            output = BytesIO()
            clean_image.save(
                output,
                format="JPEG",
                quality=85,
                optimize=True
            )

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError
    ):
        raise ValueError("Uploaded attendance selfie is not a valid image.")

    PHOTO_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PHOTO_DIR.chmod(0o700)

    filename = f"{action}_{secrets.token_hex(20)}.jpg"
    destination = PHOTO_DIR / filename

    with destination.open("xb") as file:
        file.write(output.getvalue())

    destination.chmod(0o600)

    return filename
