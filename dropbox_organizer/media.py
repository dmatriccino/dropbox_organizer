"""Media extension whitelist and excluded-folder detection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

RAW_EXTENSIONS: frozenset[str] = frozenset(
    {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".dng"}  # Canon, Nikon, DNG
)
OTHER_MEDIA_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".psd", ".png", ".mp4", ".mov"}
)
MEDIA_EXTENSIONS: frozenset[str] = RAW_EXTENSIONS | OTHER_MEDIA_EXTENSIONS

DEFAULT_EXCLUDED_DIRS: tuple[str, ...] = (
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "site-packages",
    "vendor",
    "bower_components",
    "Pods",
    ".tox",
)


def extension_of(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def is_media(path: str) -> bool:
    return extension_of(path) in MEDIA_EXTENSIONS


def excluded_folder(path: str, excluded_dirs: Iterable[str]) -> str | None:
    """Return the outermost folder of ``path`` whose name is excluded, else None.

    Matching is case-insensitive (Dropbox paths are) and only considers folder
    components, never the file name itself.
    """
    wanted = {d.lower() for d in excluded_dirs}
    if not wanted:
        return None
    folders = [p for p in path.split("/")[:-1] if p]
    for i, part in enumerate(folders):
        if part.lower() in wanted:
            return "/" + "/".join(folders[: i + 1])
    return None
