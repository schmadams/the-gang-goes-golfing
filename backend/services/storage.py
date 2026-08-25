# target path: backend/services/storage.py (new file)
import os

from backend.database import supabase


class ImageUploadError(Exception):
    """Raised when the Supabase Storage call itself fails -- most often
    because the target bucket doesn't exist yet or isn't marked Public in
    the Supabase dashboard, neither of which a SQL migration creates for
    you (a bucket is a Storage concept, not a table). Before this existed,
    that failure was an unhandled exception -- FastAPI turned it into a
    bare 500 with no body, so the frontend's generic "Couldn't upload
    that photo. Try again." was the only signal anyone had, with no way
    to tell "bucket missing" apart from a real bug without reading
    backend logs directly. Wrapping the underlying exception's own
    message (Supabase's error responses are usually specific, e.g. "Bucket
    not found") means it now reaches the API response instead.
    """


def upload_image(bucket: str, storage_path: str, file_bytes: bytes, content_type: str | None) -> str:
    """Shared upload-to-Supabase-Storage-then-return-a-public-URL helper --
    previously this exact sequence (upload with upsert, then get_public_url)
    was copy-pasted once for player profile pictures and was about to be
    copy-pasted again for club photos. Pulling it out here means the next
    thing that needs an image (post attachments, when the feed/posts
    feature gets built) has a single place to call into rather than a
    third copy -- callers still own their own bucket name, storage path
    shape, and which DB row/column the resulting URL gets written to,
    this only owns the actual upload mechanics.

    NOTE: the exact file_options key for "overwrite if it already exists"
    has changed across supabase-py versions ("upsert" vs "x-upsert"). If
    the error surfaced via ImageUploadError below mentions an unrecognized
    option rather than a missing bucket, that's what's going on -- check
    what your installed supabase-py version expects.
    """
    try:
        supabase.storage.from_(bucket).upload(
            storage_path,
            file_bytes,
            {"content-type": content_type or "image/jpeg", "upsert": "true"},
        )
        return supabase.storage.from_(bucket).get_public_url(storage_path)
    except Exception as exc:
        raise ImageUploadError(
            f"Upload to Supabase Storage bucket '{bucket}' failed: {exc}. "
            f"If this is the first upload to this bucket, check in the "
            f"Supabase dashboard that a bucket named '{bucket}' exists and "
            f"is marked Public."
        ) from exc


def extension_for(filename: str | None, default: str = ".jpg") -> str:
    """Shared "figure out a safe storage path suffix from an uploaded
    filename" helper -- same os.path.splitext(...)[1] or default fallback
    every caller of upload_image needs before it can build its own
    storage_path."""
    return os.path.splitext(filename or "")[1] or default