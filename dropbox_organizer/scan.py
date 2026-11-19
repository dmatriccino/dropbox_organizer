"""Resumable, metadata-only recursive scan of the whole Dropbox."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from dropbox.exceptions import ApiError, RateLimitError
from dropbox.files import DeletedMetadata, FileMetadata, ListFolderContinueError, ListFolderResult

from dropbox_organizer.media import excluded_folder, extension_of, is_media
from dropbox_organizer.store import ExcludedRow, MediaRow, ScanStore

log = logging.getLogger(__name__)

PAGE_LIMIT = 2000
MAX_RATE_LIMIT_RETRIES = 20
DEFAULT_BACKOFF_SECONDS = 5.0


class ListFolderClient(Protocol):
    def files_list_folder(
        self, path: str, *, recursive: bool, include_deleted: bool, limit: int
    ) -> ListFolderResult: ...

    def files_list_folder_continue(self, cursor: str) -> ListFolderResult: ...


@dataclass(frozen=True)
class ScanResult:
    pages: int
    files_seen: int
    media_kept: int
    excluded_media: int
    complete: bool


def _fmt_ts(dt: datetime | None) -> str:
    # The SDK returns naive datetimes that are UTC.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def _backoff_seconds(err: RateLimitError) -> float:
    if err.backoff is not None:
        return float(err.backoff)
    retry_after = getattr(err.error, "retry_after", None)
    if retry_after:
        return float(retry_after)
    return DEFAULT_BACKOFF_SECONDS


def _with_rate_limit_retry(
    fn: Callable[..., ListFolderResult],
    *args: Any,
    sleep: Callable[[float], None],
    **kwargs: Any,
) -> ListFolderResult:
    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except RateLimitError as err:
            if attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _backoff_seconds(err)
            log.warning(
                "Rate limited by Dropbox (attempt %d); sleeping %.1fs before retrying.",
                attempt,
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _is_cursor_reset(err: ApiError) -> bool:
    return isinstance(err.error, ListFolderContinueError) and err.error.is_reset()


def _process_page(
    result: ListFolderResult, excluded_dirs: Sequence[str]
) -> tuple[int, list[MediaRow], list[ExcludedRow], list[str]]:
    files_seen = 0
    media: list[MediaRow] = []
    excluded: list[ExcludedRow] = []
    forgotten: list[str] = []
    for entry in result.entries:
        if isinstance(entry, FileMetadata):
            files_seen += 1
            path = entry.path_display or entry.path_lower or entry.name
            if not is_media(path):
                continue
            path_lower = entry.path_lower or path.lower()
            ext = extension_of(path)
            folder = excluded_folder(path, excluded_dirs)
            if folder is not None:
                excluded.append(ExcludedRow(entry.id, path, path_lower, folder, ext))
            else:
                media.append(
                    MediaRow(
                        id=entry.id,
                        path_display=path,
                        path_lower=path_lower,
                        content_hash=entry.content_hash,
                        size_bytes=entry.size,
                        client_modified=_fmt_ts(entry.client_modified),
                        server_modified=_fmt_ts(entry.server_modified),
                        ext=ext,
                    )
                )
        elif isinstance(entry, DeletedMetadata) and entry.path_lower:
            # Something vanished mid-scan; drop it from the *local* cache.
            forgotten.append(entry.path_lower)
        # FolderMetadata and anything else: ignored.
    return files_seen, media, excluded, forgotten


def run_scan(
    client: ListFolderClient,
    store: ScanStore,
    *,
    excluded_dirs: Sequence[str],
    fresh: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    page_limit: int = PAGE_LIMIT,
) -> ScanResult:
    state = store.load_state()
    if fresh or state is None:
        state = store.reset(excluded_dirs)
        log.info("Starting a new scan.")
    elif state.complete:
        log.info(
            "Previous scan already complete (%d pages, %d files). Use --fresh to rescan.",
            state.pages,
            state.files_seen,
        )
        return _result(store)
    else:
        log.info(
            "Resuming scan from checkpoint: %d pages, %d files seen so far.",
            state.pages,
            state.files_seen,
        )
        if tuple(excluded_dirs) != state.excluded_dirs:
            log.warning(
                "Exclusion list differs from the one this scan started with; keeping the "
                "original (%s) for consistency. Use --fresh to change it.",
                ", ".join(state.excluded_dirs),
            )

    active_excludes = state.excluded_dirs
    cursor = state.cursor
    pages, files_seen = state.pages, state.files_seen

    while True:
        if cursor is None:
            result = _with_rate_limit_retry(
                client.files_list_folder,
                "",
                recursive=True,
                include_deleted=False,
                limit=page_limit,
                sleep=sleep,
            )
        else:
            try:
                result = _with_rate_limit_retry(
                    client.files_list_folder_continue, cursor, sleep=sleep
                )
            except ApiError as err:
                if not _is_cursor_reset(err):
                    raise
                log.warning("Dropbox expired the saved cursor; restarting the scan from scratch.")
                store.reset(active_excludes)
                cursor, pages, files_seen = None, 0, 0
                continue

        seen, media, excluded, forgotten = _process_page(result, active_excludes)
        store.commit_page(
            cursor=result.cursor,
            has_more=result.has_more,
            files_seen=seen,
            media=media,
            excluded=excluded,
            forgotten_paths=forgotten,
        )
        cursor = result.cursor
        pages += 1
        files_seen += seen
        log.info(
            "Page %d: %d entries | files seen %d | media kept %d | excluded media %d",
            pages,
            len(result.entries),
            files_seen,
            store.media_count(),
            store.excluded_count(),
        )
        if not result.has_more:
            break

    log.info("Scan complete.")
    return _result(store)


def _result(store: ScanStore) -> ScanResult:
    state = store.load_state()
    assert state is not None
    return ScanResult(
        pages=state.pages,
        files_seen=state.files_seen,
        media_kept=store.media_count(),
        excluded_media=store.excluded_count(),
        complete=state.complete,
    )
