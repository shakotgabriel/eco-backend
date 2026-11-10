from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_PHOTOS_PER_PARCEL = 8
MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}


def collect_uploaded_images(request):
    files = list(request.FILES.getlist("photos")) + list(request.FILES.getlist("photo"))
    seen = set()
    unique = []
    for uploaded in files:
        marker = id(uploaded)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(uploaded)
    return unique


def validate_parcel_image(uploaded):
    size = getattr(uploaded, "size", 0) or 0
    if size > MAX_PHOTO_BYTES:
        raise ValidationError("Each parcel photo must be 8 MB or smaller.")

    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError("Parcel photos must be JPEG, PNG, WebP, or GIF.")

    try:
        image = Image.open(uploaded)
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("That file is not a valid image.") from exc
    finally:
        uploaded.seek(0)

    return uploaded
