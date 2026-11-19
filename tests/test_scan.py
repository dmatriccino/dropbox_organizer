import logging

import pytest
from dropbox.exceptions import RateLimitError

from dropbox_organizer.client import ReadOnlyDropbox
from dropbox_organizer.media import DEFAULT_EXCLUDED_DIRS
from dropbox_organizer.scan import run_scan
from dropbox_organizer.store import ScanStore

from .fakes import FakeSDK, Interrupted, deleted_md, file_md, folder_md


def make_pages():
    return [
        [folder_md("/Photos"), file_md("/Photos/a.JPG"), file_md("/Photos/notes.txt")],
        [file_md("/Photos/b.cr2"), file_md("/code/node_modules/x/logo.png")],
        [file_md("/Photos/c.mov"), file_md("/code/.git/objects/d.PNG"), file_md("/x/y.zip")],
    ]


def scan(sdk, store, sleeps=None, **kw):
    sleeps = [] if sleeps is None else sleeps
    return run_scan(
        ReadOnlyDropbox(sdk),
        store,
        excluded_dirs=kw.pop("excluded_dirs", DEFAULT_EXCLUDED_DIRS),
        sleep=sleeps.append,
        **kw,
    )


def test_paginates_until_has_more_false(store):
    sdk = FakeSDK(make_pages())
    result = scan(sdk, store)

    assert sdk.calls[0] == (
        "files_list_folder",
        {"path": "", "recursive": True, "include_deleted": False, "limit": 2000},
    )
    assert [c for c in sdk.calls[1:]] == [
        ("files_list_folder_continue", "cursor-1"),
        ("files_list_folder_continue", "cursor-2"),
    ]
    assert result.complete
    assert result.pages == 3
    assert result.files_seen == 7  # folders are not files
    assert result.media_kept == 3
    assert result.excluded_media == 2
    assert {m.path_display for m in store.iter_media()} == {
        "/Photos/a.JPG",
        "/Photos/b.cr2",
        "/Photos/c.mov",
    }


def test_excluded_media_counted_by_folder(store):
    scan(FakeSDK(make_pages()), store)
    assert store.excluded_by_folder() == [("/code/.git", 1), ("/code/node_modules", 1)]


def test_custom_exclusion_list(store):
    result = scan(FakeSDK(make_pages()), store, excluded_dirs=("Photos",))
    assert result.excluded_media == 3
    assert {m.path_display for m in store.iter_media()} == {
        "/code/node_modules/x/logo.png",
        "/code/.git/objects/d.PNG",
    }


def test_interrupted_scan_resumes_from_checkpoint(tmp_path):
    db = tmp_path / "scan.sqlite"
    sdk = FakeSDK(make_pages())
    sdk.fail_on_page = 2

    with ScanStore(db) as store, pytest.raises(Interrupted):
        scan(sdk, store)

    with ScanStore(db) as store:
        state = store.load_state()
        assert state.cursor == "cursor-2"
        assert not state.complete
        assert state.pages == 2
        assert state.files_seen == 4

    sdk.calls.clear()
    with ScanStore(db) as store:
        result = scan(sdk, store)

    # Resumed with continue, never restarted with files_list_folder.
    assert sdk.calls == [("files_list_folder_continue", "cursor-2")]
    assert result.complete
    assert (result.pages, result.files_seen, result.media_kept, result.excluded_media) == (
        3,
        7,
        3,
        2,
    )


def test_completed_scan_is_not_repeated_unless_fresh(store):
    sdk = FakeSDK(make_pages())
    scan(sdk, store)
    sdk.calls.clear()
    scan(sdk, store)
    assert sdk.calls == []

    result = scan(sdk, store, fresh=True)
    assert sdk.calls[0][0] == "files_list_folder"
    assert result.files_seen == 7  # counters reset, not doubled


def test_resume_keeps_original_exclusions(tmp_path, caplog):
    db = tmp_path / "scan.sqlite"
    sdk = FakeSDK(make_pages())
    sdk.fail_on_page = 1
    with ScanStore(db) as store, pytest.raises(Interrupted):
        scan(sdk, store)
    with ScanStore(db) as store, caplog.at_level(logging.WARNING):
        result = scan(sdk, store, excluded_dirs=())
    assert "Exclusion list differs" in caplog.text
    assert result.excluded_media == 2


def test_rate_limit_sleeps_backoff_then_retries(store):
    sdk = FakeSDK(make_pages())
    sdk.rate_limit_on_page = {0: 7.0, 2: None}
    sleeps: list[float] = []
    result = scan(sdk, store, sleeps)
    assert sleeps == [7.0, 5.0]  # explicit backoff, then default when none given
    assert result.complete and result.files_seen == 7


def test_rate_limit_gives_up_eventually(store, monkeypatch):
    monkeypatch.setattr("dropbox_organizer.scan.MAX_RATE_LIMIT_RETRIES", 3)

    class AlwaysLimited(FakeSDK):
        def files_list_folder(self, *a, **k):
            raise RateLimitError("r", backoff=1)

    sleeps: list[float] = []
    with pytest.raises(RateLimitError):
        scan(AlwaysLimited([]), store, sleeps)
    assert sleeps == [1.0, 1.0]


def test_expired_cursor_restarts_scan(tmp_path):
    db = tmp_path / "scan.sqlite"
    sdk = FakeSDK(make_pages())
    sdk.fail_on_page = 2
    with ScanStore(db) as store, pytest.raises(Interrupted):
        scan(sdk, store)

    sdk.reset_cursor_on = "cursor-2"
    sdk.calls.clear()
    with ScanStore(db) as store:
        result = scan(sdk, store)
    assert [c[0] for c in sdk.calls] == [
        "files_list_folder_continue",
        "files_list_folder",
        "files_list_folder_continue",
        "files_list_folder_continue",
    ]
    assert (result.pages, result.files_seen, result.media_kept) == (3, 7, 3)


def test_deleted_entries_drop_from_local_cache_only(store):
    pages = [
        [file_md("/Photos/a.jpg"), file_md("/Photos/sub/b.jpg"), file_md("/Photos/subway.jpg")],
        [deleted_md("/Photos/sub")],
    ]
    sdk = FakeSDK(pages)
    scan(sdk, store)
    assert {m.path_display for m in store.iter_media()} == {"/Photos/a.jpg", "/Photos/subway.jpg"}
    # And the fake never saw any non-read call (it has no such methods anyway).
    assert {c[0] for c in sdk.calls} <= {"files_list_folder", "files_list_folder_continue"}


def test_moved_file_keyed_by_id(store):
    pages = [
        [file_md("/Photos/a.jpg", file_id="id:same")],
        [file_md("/B/a.jpg", file_id="id:same")],
    ]
    scan(FakeSDK(pages), store)
    assert [m.path_display for m in store.iter_media()] == ["/B/a.jpg"]
