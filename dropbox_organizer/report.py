"""Build duplicate groups from the local SQLite cache and write CSV + summary."""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dropbox_organizer.store import MediaRow, ScanStore

STATUS_DUPLICATE = "DUPLICATE"
STATUS_SIZE_MISMATCH = "SIZE_MISMATCH"

CSV_COLUMNS = (
    "group_id",
    "status",
    "content_hash",
    "size_bytes",
    "path_display",
    "client_modified",
    "server_modified",
    "suggested_keep",
)


@dataclass
class DuplicateGroup:
    group_id: int
    content_hash: str
    status: str
    files: list[MediaRow]
    keep: MediaRow | None = None

    @property
    def redundant(self) -> list[MediaRow]:
        return [f for f in self.files if f is not self.keep] if self.keep else []

    @property
    def reclaimable_bytes(self) -> int:
        return sum(f.size_bytes for f in self.redundant)


def choose_keep(files: Iterable[MediaRow]) -> MediaRow:
    """Oldest client_modified wins; tie-break on shortest path, then path text."""
    return min(files, key=lambda f: (f.client_modified, len(f.path_display), f.path_lower))


def build_groups(files: Iterable[MediaRow]) -> list[DuplicateGroup]:
    by_hash: dict[str, list[MediaRow]] = defaultdict(list)
    for f in files:
        if f.content_hash:
            by_hash[f.content_hash].append(f)

    groups: list[DuplicateGroup] = []
    for content_hash, members in by_hash.items():
        if len(members) < 2:
            continue
        if len({m.size_bytes for m in members}) == 1:
            keep = choose_keep(members)
            ordered = [keep, *sorted((m for m in members if m is not keep), key=_path_key)]
            groups.append(DuplicateGroup(0, content_hash, STATUS_DUPLICATE, ordered, keep))
        else:
            ordered = sorted(members, key=_path_key)
            groups.append(DuplicateGroup(0, content_hash, STATUS_SIZE_MISMATCH, ordered, None))

    # Biggest wins first; mismatches at the end.
    groups.sort(key=lambda g: (g.status != STATUS_DUPLICATE, -g.reclaimable_bytes, g.content_hash))
    for i, g in enumerate(groups, start=1):
        g.group_id = i
    return groups


def _path_key(f: MediaRow) -> str:
    return f.path_lower


def write_duplicates_csv(groups: Iterable[DuplicateGroup], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for g in groups:
            for f in g.files:
                writer.writerow(
                    (
                        g.group_id,
                        g.status,
                        g.content_hash,
                        f.size_bytes,
                        f.path_display,
                        f.client_modified,
                        f.server_modified,
                        "true" if f is g.keep else "false",
                    )
                )


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass
class Summary:
    scan_complete: bool
    scan_started_at: str
    scan_updated_at: str
    files_scanned: int
    media_kept: int
    media_without_hash: int
    excluded_dirs: tuple[str, ...]
    excluded_media: int
    excluded_folders: list[tuple[str, int]]
    duplicate_groups: int
    duplicate_files: int
    redundant_copies: int
    reclaimable_bytes: int
    size_mismatch_groups: int
    by_extension: dict[str, dict[str, int]] = field(default_factory=dict)

    def render(self) -> str:
        lines = ["Dropbox duplicate media report (READ-ONLY: nothing was changed)", ""]
        if not self.scan_complete:
            lines += [
                "!! WARNING: the scan is INCOMPLETE. Run `scan` again to finish it;",
                "!! these numbers only cover what has been listed so far.",
                "",
            ]
        mismatch_note = (
            "  <-- NOT treated as duplicates; investigate" if self.size_mismatch_groups else ""
        )
        stats = [
            ("Scan started", self.scan_started_at),
            ("Scan last updated", self.scan_updated_at),
            ("Files scanned", f"{self.files_scanned:,}"),
            ("Media files kept", f"{self.media_kept:,}"),
            ("Media without content_hash", f"{self.media_without_hash:,}"),
            ("Excluded media files", f"{self.excluded_media:,}"),
            ("Duplicate groups", f"{self.duplicate_groups:,}"),
            ("Duplicate files (in groups)", f"{self.duplicate_files:,}"),
            ("Redundant copies", f"{self.redundant_copies:,}"),
            (
                "Reclaimable",
                f"{human_bytes(self.reclaimable_bytes)} ({self.reclaimable_bytes:,} bytes)",
            ),
            ("SIZE_MISMATCH groups", f"{self.size_mismatch_groups:,}{mismatch_note}"),
        ]
        lines += [f"{label + ':':<29} {value}" for label, value in stats]
        lines += [
            "",
            "Excluded folder names: " + (", ".join(self.excluded_dirs) or "(none)"),
            "Folders whose media was skipped (check nothing real is in here):",
        ]
        if self.excluded_folders:
            lines += [f"  {count:>8,}  {folder}" for folder, count in self.excluded_folders]
        else:
            lines.append("  (none)")
        lines += ["", "Breakdown by extension:"]
        lines.append(
            f"  {'ext':<7}{'media':>10}{'excluded':>10}{'in groups':>11}"
            f"{'redundant':>11}{'reclaimable':>16}"
        )
        for ext in sorted(self.by_extension):
            row = self.by_extension[ext]
            lines.append(
                f"  {ext:<7}{row['media']:>10,}{row['excluded']:>10,}{row['in_groups']:>11,}"
                f"{row['redundant']:>11,}{human_bytes(row['reclaimable']):>16}"
            )
        lines += [
            "",
            "suggested_keep is only a suggestion (oldest client_modified, then shortest path).",
            "This tool has no ability to delete anything; review the CSV yourself.",
        ]
        return "\n".join(lines) + "\n"


def build_summary(store: ScanStore, groups: list[DuplicateGroup]) -> Summary:
    state = store.load_state()
    if state is None:
        raise RuntimeError("No scan found in the database. Run `scan` first.")

    media = list(store.iter_media())
    by_ext: dict[str, dict[str, int]] = defaultdict(
        lambda: {"media": 0, "excluded": 0, "in_groups": 0, "redundant": 0, "reclaimable": 0}
    )
    for f in media:
        by_ext[f.ext]["media"] += 1
    for ext, count in store.excluded_by_ext():
        by_ext[ext]["excluded"] += count

    dupes = [g for g in groups if g.status == STATUS_DUPLICATE]
    for g in dupes:
        for f in g.files:
            by_ext[f.ext]["in_groups"] += 1
        for f in g.redundant:
            by_ext[f.ext]["redundant"] += 1
            by_ext[f.ext]["reclaimable"] += f.size_bytes

    return Summary(
        scan_complete=state.complete,
        scan_started_at=state.started_at,
        scan_updated_at=state.updated_at,
        files_scanned=state.files_seen,
        media_kept=len(media),
        media_without_hash=sum(1 for f in media if not f.content_hash),
        excluded_dirs=state.excluded_dirs,
        excluded_media=store.excluded_count(),
        excluded_folders=store.excluded_by_folder(),
        duplicate_groups=len(dupes),
        duplicate_files=sum(len(g.files) for g in dupes),
        redundant_copies=sum(len(g.redundant) for g in dupes),
        reclaimable_bytes=sum(g.reclaimable_bytes for g in dupes),
        size_mismatch_groups=sum(1 for g in groups if g.status == STATUS_SIZE_MISMATCH),
        by_extension=dict(by_ext),
    )


def write_report(
    store: ScanStore, reports_dir: Path, now: datetime | None = None
) -> tuple[Path, Path, Summary]:
    groups = build_groups(store.iter_media())
    summary = build_summary(store, groups)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    csv_path = reports_dir / f"duplicates_{stamp}.csv"
    summary_path = reports_dir / f"summary_{stamp}.txt"
    write_duplicates_csv(groups, csv_path)
    summary_path.write_text(summary.render(), encoding="utf-8")
    return csv_path, summary_path, summary


__all__ = [
    "CSV_COLUMNS",
    "STATUS_DUPLICATE",
    "STATUS_SIZE_MISMATCH",
    "DuplicateGroup",
    "Summary",
    "build_groups",
    "build_summary",
    "choose_keep",
    "human_bytes",
    "write_duplicates_csv",
    "write_report",
]
