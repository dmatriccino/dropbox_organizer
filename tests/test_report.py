import csv
from datetime import UTC, datetime

from dropbox_organizer.report import (
    CSV_COLUMNS,
    STATUS_DUPLICATE,
    STATUS_SIZE_MISMATCH,
    build_groups,
    choose_keep,
    human_bytes,
    write_report,
)
from dropbox_organizer.store import ExcludedRow, MediaRow


def row(path, *, h="A", size=10, cm="2020-01-01T00:00:00Z", fid=None):
    return MediaRow(
        id=fid or f"id:{path}",
        path_display=path,
        path_lower=path.lower(),
        content_hash=h,
        size_bytes=size,
        client_modified=cm,
        server_modified="2021-01-01T00:00:00Z",
        ext="." + path.rsplit(".", 1)[-1].lower(),
    )


def test_only_groups_with_two_or_more():
    groups = build_groups([row("/a.jpg", h="A"), row("/b.jpg", h="B"), row("/c.jpg", h="A")])
    assert len(groups) == 1
    assert {f.path_display for f in groups[0].files} == {"/a.jpg", "/c.jpg"}
    assert groups[0].status == STATUS_DUPLICATE


def test_files_without_hash_never_grouped():
    assert build_groups([row("/a.jpg", h=None), row("/b.jpg", h=None)]) == []


def test_keep_oldest_client_modified():
    files = [
        row("/a.jpg", cm="2022-01-01T00:00:00Z"),
        row("/zzzzzzzzzz/b.jpg", cm="2019-05-01T00:00:00Z"),
        row("/c.jpg", cm="2020-01-01T00:00:00Z"),
    ]
    assert choose_keep(files).path_display == "/zzzzzzzzzz/b.jpg"


def test_keep_tie_breaks_on_shortest_path():
    files = [row("/long/path/a.jpg"), row("/x/a.jpg"), row("/longer/a.jpg")]
    assert choose_keep(files).path_display == "/x/a.jpg"


def test_exactly_one_keep_per_duplicate_group():
    groups = build_groups([row("/a.jpg"), row("/b.jpg"), row("/c.jpg")])
    assert groups[0].keep is groups[0].files[0]
    assert len(groups[0].redundant) == 2
    assert groups[0].reclaimable_bytes == 20


def test_size_mismatch_flagged_not_duplicate():
    groups = build_groups([row("/a.jpg", size=10), row("/b.jpg", size=11)])
    assert groups[0].status == STATUS_SIZE_MISMATCH
    assert groups[0].keep is None
    assert groups[0].reclaimable_bytes == 0


def test_group_ordering_and_ids():
    groups = build_groups(
        [
            row("/m1.jpg", h="M", size=1),
            row("/m2.jpg", h="M", size=2),
            row("/s1.jpg", h="S", size=5),
            row("/s2.jpg", h="S", size=5),
            row("/b1.jpg", h="B", size=500),
            row("/b2.jpg", h="B", size=500),
        ]
    )
    assert [(g.group_id, g.content_hash) for g in groups] == [(1, "B"), (2, "S"), (3, "M")]


def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1536) == "1.50 KiB"
    assert human_bytes(5 * 1024**3) == "5.00 GiB"


def _seed(store, media, excluded=(), complete=True):
    store.reset(("node_modules",))
    store.commit_page(
        cursor="c1",
        has_more=not complete,
        files_seen=len(media) + len(excluded) + 3,
        media=media,
        excluded=excluded,
    )


def test_write_report_end_to_end(store, tmp_path):
    media = [
        row("/Photos/2019/IMG_1.CR2", h="X", size=2048, cm="2019-01-01T00:00:00Z"),
        row("/Backup/IMG_1.CR2", h="X", size=2048, cm="2019-01-01T00:00:00Z"),
        row("/Old/IMG_1.cr2", h="X", size=2048, cm="2023-01-01T00:00:00Z"),
        row("/a.jpg", h="J", size=100),
        row("/b.jpg", h="J", size=101),
        row("/unique.mov", h="U", size=9999),
    ]
    excluded = [
        ExcludedRow(
            "id:e", "/p/node_modules/x.png", "/p/node_modules/x.png", "/p/node_modules", ".png"
        )
    ]
    _seed(store, media, excluded)
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
    csv_path, summary_path, summary = write_report(store, tmp_path / "reports", now)

    assert csv_path.name == "duplicates_20261119_120000.csv"
    assert summary_path.name == "summary_20261119_120000.txt"

    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 5
    dup = [r for r in rows if r["status"] == STATUS_DUPLICATE]
    assert [r["suggested_keep"] for r in dup] == ["true", "false", "false"]
    assert dup[0]["path_display"] == "/Backup/IMG_1.CR2"  # same date, shorter path
    mismatch = [r for r in rows if r["status"] == STATUS_SIZE_MISMATCH]
    assert {r["suggested_keep"] for r in mismatch} == {"false"}

    assert summary.files_scanned == 10
    assert summary.media_kept == 6
    assert summary.excluded_media == 1
    assert summary.excluded_folders == [("/p/node_modules", 1)]
    assert summary.duplicate_groups == 1
    assert summary.duplicate_files == 3
    assert summary.redundant_copies == 2
    assert summary.reclaimable_bytes == 4096
    assert summary.size_mismatch_groups == 1
    assert summary.by_extension[".cr2"] == {
        "media": 3,
        "excluded": 0,
        "in_groups": 3,
        "redundant": 2,
        "reclaimable": 4096,
    }
    assert summary.by_extension[".png"]["excluded"] == 1

    text = summary_path.read_text()
    assert "4.00 KiB" in text
    assert "/p/node_modules" in text
    assert "INCOMPLETE" not in text


def test_incomplete_scan_is_flagged(store, tmp_path):
    _seed(store, [row("/a.jpg")], complete=False)
    _, summary_path, _ = write_report(store, tmp_path)
    assert "INCOMPLETE" in summary_path.read_text()
