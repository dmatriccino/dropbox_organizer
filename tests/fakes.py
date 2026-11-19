"""Fake Dropbox SDK pieces. Uses the SDK's real metadata types; no network."""

from __future__ import annotations

import itertools
from datetime import datetime

from dropbox.exceptions import ApiError, RateLimitError
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
    ListFolderContinueError,
    ListFolderResult,
)

_ids = itertools.count(1)


def file_md(
    path: str,
    *,
    content_hash: str | None = "h" * 64,
    size: int = 100,
    client_modified: datetime = datetime(2020, 1, 1),
    server_modified: datetime = datetime(2021, 1, 1),
    file_id: str | None = None,
) -> FileMetadata:
    return FileMetadata(
        name=path.rsplit("/", 1)[-1],
        id=file_id or f"id:{next(_ids):08d}",
        client_modified=client_modified,
        server_modified=server_modified,
        rev="0123456789abcdef",
        size=size,
        path_lower=path.lower(),
        path_display=path,
        content_hash=content_hash,
    )


def folder_md(path: str) -> FolderMetadata:
    return FolderMetadata(
        name=path.rsplit("/", 1)[-1],
        id=f"id:{next(_ids):08d}",
        path_lower=path.lower(),
        path_display=path,
    )


def deleted_md(path: str) -> DeletedMetadata:
    return DeletedMetadata(name=path.rsplit("/", 1)[-1], path_lower=path.lower(), path_display=path)


class Interrupted(RuntimeError):
    pass


class FakeSDK:
    """Serves pre-baked pages. Records every call so tests can assert on them.

    Deliberately has *only* the read-only methods: if production code called
    anything else, tests would blow up with AttributeError.
    """

    def __init__(self, pages: list[list[object]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, object]] = []
        self.fail_on_page: int | None = None  # raise Interrupted when serving this index
        self.rate_limit_on_page: dict[int, float | None] = {}
        self.reset_cursor_on: str | None = None

    def _page(self, index: int) -> ListFolderResult:
        if self.fail_on_page == index:
            self.fail_on_page = None
            raise Interrupted(f"boom on page {index}")
        if index in self.rate_limit_on_page:
            backoff = self.rate_limit_on_page.pop(index)
            raise RateLimitError("req-1", backoff=backoff)
        has_more = index + 1 < len(self.pages)
        return ListFolderResult(
            entries=self.pages[index], cursor=f"cursor-{index + 1}", has_more=has_more
        )

    def files_list_folder(self, path, *, recursive, include_deleted, limit):
        self.calls.append(
            (
                "files_list_folder",
                {
                    "path": path,
                    "recursive": recursive,
                    "include_deleted": include_deleted,
                    "limit": limit,
                },
            )
        )
        return self._page(0)

    def files_list_folder_continue(self, cursor):
        self.calls.append(("files_list_folder_continue", cursor))
        if self.reset_cursor_on == cursor:
            self.reset_cursor_on = None
            raise ApiError("req-2", ListFolderContinueError.reset, "reset", None)
        return self._page(int(cursor.split("-")[1]))

    def users_get_current_account(self):
        self.calls.append(("users_get_current_account", None))
        return "account"
