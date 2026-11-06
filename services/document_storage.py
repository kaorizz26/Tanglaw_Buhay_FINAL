"""Check uploaded documents and keep them in a private folder.

Files are stored outside the project and outside static/, under random names, and are
only ever read back through an authorized route. The database holds the random name,
never a path, and the citizen's own file name is used for display only.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import secrets
from typing import NamedTuple

from flask import current_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_DISPLAY_NAME_LENGTH = 100
# Must match the storage_key check in migration 0017.
STORAGE_KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.(pdf|jpg|png)")

# What each accepted file type must start with. The file's own bytes are checked
# because a file name or a browser-reported type can be changed by the sender.
FILE_TYPES = {
    "pdf": {"content_type": "application/pdf", "signature": b"%PDF-"},
    "jpg": {"content_type": "image/jpeg", "signature": b"\xff\xd8\xff"},
    "png": {"content_type": "image/png", "signature": b"\x89PNG\r\n\x1a\n"},
}
EXTENSION_TYPES = {"pdf": "pdf", "jpg": "jpg", "jpeg": "jpg", "png": "png"}

# Plain-English messages for the person uploading; nothing technical or private.
UPLOAD_MESSAGES = {
    "no_file": "Choose a file to upload.",
    "empty": "The file is empty. Choose a different file.",
    "too_large": "The file is larger than 5 MB. Choose a smaller file.",
    "wrong_type": "Upload a PDF, JPG or PNG file.",
    "not_matching": "The file does not match its type. Upload a real PDF, JPG or PNG file.",
}


class DocumentUploadError(ValueError):
    """The uploaded file cannot be accepted; `code` names the reason and the text is safe to show."""

    def __init__(self, code):
        super().__init__(UPLOAD_MESSAGES[code])
        self.code = code


class DocumentStorageError(RuntimeError):
    """Private storage is not set up or a stored file is missing; the message is a fixed code."""


class ValidatedUpload(NamedTuple):
    """A file that passed every check and is ready to be stored."""

    display_name: str
    extension: str
    content_type: str
    data: bytes
    size_bytes: int
    sha256: str


# ---- Checking an upload ---------------------------------------------------------

def clean_display_name(file_name, extension):
    """Make the citizen's file name safe to show: no folders, no control characters.

    It is only a label. Stored files are named by a random key, never by this text.
    """
    name = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable())
    name = " ".join(name.split())
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        stem, dot, suffix = name.rpartition(".")
        keep = MAX_DISPLAY_NAME_LENGTH - len(dot) - len(suffix)
        name = (stem[:keep] + dot + suffix) if dot and keep > 0 else name[:MAX_DISPLAY_NAME_LENGTH]
    return name if name.strip(". ") else f"document.{extension}"


def validate_upload(file_name, stream, max_bytes=DEFAULT_MAX_BYTES):
    """Return a ValidatedUpload, or raise DocumentUploadError.

    Checks the file name's extension, the size (reading at most one byte over the
    limit, so a huge file is never held in memory) and the file's opening bytes.
    The type reported by the browser is ignored.
    """
    if not file_name or stream is None:
        raise DocumentUploadError("no_file")
    _, dot, suffix = file_name.rpartition(".")
    extension = EXTENSION_TYPES.get(suffix.lower()) if dot else None
    if extension is None:
        raise DocumentUploadError("wrong_type")
    data = stream.read(max_bytes + 1)
    if not data:
        raise DocumentUploadError("empty")
    if len(data) > max_bytes:
        raise DocumentUploadError("too_large")
    if not data.startswith(FILE_TYPES[extension]["signature"]):
        raise DocumentUploadError("not_matching")
    return ValidatedUpload(
        display_name=clean_display_name(file_name, extension), extension=extension,
        content_type=FILE_TYPES[extension]["content_type"], data=data,
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
    )


# ---- Private storage -----------------------------------------------------------

def storage_root():
    """Return the private storage folder, or raise if it is missing or unsafe.

    It must be an existing absolute folder outside the project, so uploads can never
    be reached as static files or end up in the repository.
    """
    configured = current_app.config.get("DOCUMENT_STORAGE_DIR")
    if not isinstance(configured, str) or not configured.strip():
        raise DocumentStorageError("not_configured")
    root = Path(configured.strip())
    if not root.is_absolute() or not root.is_dir():
        raise DocumentStorageError("not_configured")
    root = root.resolve()
    static_folder = current_app.static_folder
    unsafe_places = [PROJECT_ROOT] + ([Path(static_folder).resolve()] if static_folder else [])
    if any(root == place or root.is_relative_to(place) for place in unsafe_places):
        raise DocumentStorageError("not_configured")
    return root


def _path_for_key(storage_key):
    """Return the file path for a storage key, refusing anything but our own random names."""
    if not isinstance(storage_key, str) or not STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise DocumentStorageError("invalid_key")
    return storage_root() / storage_key


def new_storage_key(extension):
    """Return an unguessable name such as '3f2a...c9.pdf' that tells nothing about the file."""
    return f"{secrets.token_hex(16)}.{extension}"


def delete_stored_file(storage_key):
    """Remove a stored file; a file that is already gone is not an error."""
    try:
        _path_for_key(storage_key).unlink()
    except FileNotFoundError:
        pass


@contextmanager
def stored_upload(upload):
    """Save a validated upload and yield its storage key; remove the file if the block fails.

    Use it around the database insert so a failed upload leaves no orphaned file:

        with stored_upload(upload) as key:
            with connection_factory() as connection:   # commits when this block ends
                ...insert the file record using key...

    The connection block must be inside, so a failed commit also removes the file.
    """
    storage_key = new_storage_key(upload.extension)
    path = _path_for_key(storage_key)
    # O_EXCL refuses to overwrite an existing file (and then nothing is cleaned up, since
    # the file is not ours); 0o600 keeps it private to the app's account; O_BINARY
    # (Windows only) stops line endings being rewritten.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stored:
            stored.write(upload.data)
            stored.flush()
            os.fsync(stored.fileno())
        yield storage_key
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def stored_file_path(storage_key):
    """Return the path of a stored file for an authorized download, or raise if it is missing.

    The path must resolve to a regular file directly inside the storage folder. This
    refuses a symbolic link planted in the folder that points somewhere else.
    """
    path = _path_for_key(storage_key)
    if path.is_symlink() or not path.is_file() or path.resolve().parent != storage_root():
        raise DocumentStorageError("missing")
    return path
