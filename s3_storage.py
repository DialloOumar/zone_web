"""Linode Object Storage helpers for shift photos.

All operations are best-effort: failures are logged and return None / False so
callers can save the shift even if the photo round-trip stumbles.
"""
import io
import logging
import os
import time
import uuid
from typing import Optional, Tuple

# boto3 / Pillow are optional at import time: if they're missing (a minimal
# environment), the module still loads and simply reports "not configured" —
# photo features switch off instead of crashing the whole app on startup.
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - exercised only where boto3 is absent
    boto3 = None
    class BotoCoreError(Exception): pass
    class ClientError(Exception): pass

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover
    Image = ImageOps = None
    class UnidentifiedImageError(Exception): pass

# Reasons returned alongside None when upload_shift_photo fails. The caller
# maps these to user-facing strings via the translation table.
ERR_NOT_CONFIGURED = "not_configured"
ERR_TOO_LARGE      = "too_large"
ERR_BAD_FORMAT     = "bad_format"
ERR_S3             = "s3_error"
ERR_UNKNOWN        = "unknown"

log = logging.getLogger(__name__)

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")
S3_REGION       = os.environ.get("S3_REGION", "")
S3_BUCKET       = os.environ.get("S3_BUCKET", "")
S3_ACCESS_KEY   = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY   = os.environ.get("S3_SECRET_KEY", "")
# Per-environment folder inside the bucket (e.g. "prod/" or "test/").
# Empty string means objects land at the bucket root.
S3_PREFIX       = os.environ.get("S3_PREFIX", "")
if S3_PREFIX and not S3_PREFIX.endswith("/"):
    S3_PREFIX += "/"

PHOTO_PREFIX    = S3_PREFIX + "shift-photos/"
VEHICLE_PREFIX  = S3_PREFIX + "vehicles/"      # one photo per vehicle
CITERNE_PREFIX  = S3_PREFIX + "citernes/"      # one photo per citerne
MAX_BYTES       = 12 * 1024 * 1024   # 12 MB raw upload cap
RESIZE_MAX      = 1920               # longest edge after resize
JPEG_QUALITY    = 85
SIGNED_URL_TTL  = 3600               # 1 hour


def _client():
    """Lazily build an S3 client; returns None if not configured."""
    if boto3 is None:
        return None
    if not all([S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY]):
        return None
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        region_name=S3_REGION or None,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def is_configured() -> bool:
    return _client() is not None


def upload_shift_photo(file_storage, machine_id: str, date_str: str, shift: str) -> Tuple[Optional[str], Optional[str]]:
    """Resize and upload a shift photo.

    Returns a (key, error_code) pair. On success: (key, None). On failure:
    (None, error_code) — the caller maps the code to a localized message so
    supervisors know whether to retry, change the photo, or call support.
    """
    s3 = _client()
    if s3 is None:
        log.warning("S3 not configured; skipping photo upload")
        return None, ERR_NOT_CONFIGURED

    try:
        # Read with a hard byte cap so we don't OOM on a 50 MB upload
        raw = file_storage.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            log.warning("Photo exceeds %d bytes; rejected", MAX_BYTES)
            return None, ERR_TOO_LARGE

        img = Image.open(io.BytesIO(raw))
        # Apply EXIF orientation tag (phones tag rotation rather than baking it
        # in; without this, portrait photos display sideways after JPEG re-save).
        img = ImageOps.exif_transpose(img)
        img.thumbnail((RESIZE_MAX, RESIZE_MAX))
        if img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        buf.seek(0)

        # Key format: shift-photos/YYYY-MM-DD/MACHINE-shift-{ts}-{rand}.jpg
        key = (
            f"{PHOTO_PREFIX}{date_str}/{machine_id}-{shift}"
            f"-{int(time.time())}-{uuid.uuid4().hex[:6]}.jpg"
        )
        s3.put_object(
            Bucket=S3_BUCKET, Key=key, Body=buf.getvalue(),
            ContentType="image/jpeg",
        )
        return key, None
    except UnidentifiedImageError:
        log.warning("Uploaded file is not a recognized image (likely HEIC or corrupted upload)")
        return None, ERR_BAD_FORMAT
    except (BotoCoreError, ClientError) as e:
        log.exception("S3 upload failed: %s", e)
        return None, ERR_S3
    except Exception as e:
        log.exception("Unexpected error during photo upload: %s", e)
        return None, ERR_UNKNOWN


def _resize_to_jpeg(file_storage) -> Tuple[Optional[bytes], Optional[str]]:
    """Read an uploaded image (capped), fix EXIF rotation, downscale and
    re-encode to JPEG. Returns (jpeg_bytes, None) or (None, error_code)."""
    raw = file_storage.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        log.warning("Photo exceeds %d bytes; rejected", MAX_BYTES)
        return None, ERR_TOO_LARGE
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((RESIZE_MAX, RESIZE_MAX))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), None


def _slug(text: str) -> str:
    """A filesystem-safe fragment for an object key (letters/digits/dash)."""
    out = "".join(ch if (ch.isalnum() or ch == "-") else "-" for ch in (text or "").lower())
    out = "-".join(p for p in out.split("-") if p)
    return out or "vehicle"


def _upload_photo(file_storage, prefix: str, code: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Resize and upload an image under `prefix`. Returns (key, None) on
    success or (None, error_code) on failure — the caller maps the code to a
    localized message. Best-effort: an S3 hiccup never has to block the save."""
    s3 = _client()
    if s3 is None:
        log.warning("S3 not configured; skipping photo upload")
        return None, ERR_NOT_CONFIGURED
    try:
        body, err = _resize_to_jpeg(file_storage)
        if err:
            return None, err
        key = f"{prefix}{_slug(code)}-{int(time.time())}-{uuid.uuid4().hex[:6]}.jpg"
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="image/jpeg")
        return key, None
    except UnidentifiedImageError:
        log.warning("Uploaded photo is not a recognized image (HEIC or corrupted)")
        return None, ERR_BAD_FORMAT
    except (BotoCoreError, ClientError) as e:
        log.exception("S3 photo upload failed: %s", e)
        return None, ERR_S3
    except Exception as e:
        log.exception("Unexpected error during photo upload: %s", e)
        return None, ERR_UNKNOWN


def upload_vehicle_photo(file_storage, code: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Resize and upload a vehicle photo to {S3_PREFIX}vehicles/."""
    return _upload_photo(file_storage, VEHICLE_PREFIX, code)


def upload_citerne_photo(file_storage, code: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Resize and upload a citerne photo to {S3_PREFIX}citernes/."""
    return _upload_photo(file_storage, CITERNE_PREFIX, code)


def signed_url(photo_key: str, expires_in: int = SIGNED_URL_TTL) -> Optional[str]:
    """Return a time-limited URL for displaying a private object."""
    s3 = _client()
    if s3 is None or not photo_key:
        return None
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": photo_key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as e:
        log.exception("Failed to sign URL for %s: %s", photo_key, e)
        return None


def delete_photo(photo_key: str) -> bool:
    """Best-effort deletion. Returns True on success."""
    s3 = _client()
    if s3 is None or not photo_key:
        return False
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=photo_key)
        return True
    except (BotoCoreError, ClientError) as e:
        log.exception("Failed to delete %s: %s", photo_key, e)
        return False
