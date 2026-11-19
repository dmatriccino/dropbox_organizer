import pytest

from dropbox_organizer.media import (
    DEFAULT_EXCLUDED_DIRS,
    MEDIA_EXTENSIONS,
    excluded_folder,
    extension_of,
    is_media,
)


@pytest.mark.parametrize(
    "path",
    [
        "/a/IMG_0001.CR2",
        "/a/x.cr3",
        "/a/x.CRW",
        "/a/x.nef",
        "/a/x.NRW",
        "/a/x.Dng",
        "/a/x.JPG",
        "/a/x.jpeg",
        "/a/x.HEIC",
        "/a/x.tif",
        "/a/x.TIFF",
        "/a/x.psd",
        "/a/x.Png",
        "/a/x.MP4",
        "/a/x.mov",
    ],
)
def test_whitelisted_case_insensitive(path):
    assert is_media(path)


@pytest.mark.parametrize(
    "path", ["/a/notes.txt", "/a/x.gif", "/a/x.arw", "/a/x.jpg.bak", "/a/jpg", "/a/x.mkv"]
)
def test_not_media(path):
    assert not is_media(path)


def test_whitelist_is_exactly_the_spec():
    assert {
        ".cr2",
        ".cr3",
        ".crw",
        ".nef",
        ".nrw",
        ".dng",
        ".jpg",
        ".jpeg",
        ".heic",
        ".tif",
        ".tiff",
        ".psd",
        ".png",
        ".mp4",
        ".mov",
    } == MEDIA_EXTENSIONS


def test_extension_of_lowercases():
    assert extension_of("/Photos/A.B.JPEG") == ".jpeg"


def test_default_excludes():
    assert set(DEFAULT_EXCLUDED_DIRS) == {
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
    }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/code/app/node_modules/pkg/logo.png", "/code/app/node_modules"),
        ("/code/ios/pods/Lib/icon.PNG", "/code/ios/pods"),  # case-insensitive
        ("/code/ios/Pods/Lib/icon.png", "/code/ios/Pods"),
        ("/x/node_modules/a/node_modules/b.png", "/x/node_modules"),  # outermost wins
        ("/Photos/2020/vendor.jpg", None),  # file name is never a folder match
        ("/Photos/vendors/a.jpg", None),  # whole-component match only
        ("/Photos/2020/a.jpg", None),
    ],
)
def test_excluded_folder(path, expected):
    assert excluded_folder(path, DEFAULT_EXCLUDED_DIRS) == expected


def test_no_excludes():
    assert excluded_folder("/node_modules/a.png", ()) is None
