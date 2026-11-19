"""The only place the Dropbox SDK client is constructed.

ReadOnlyDropbox deliberately exposes exactly three read-only API calls and
nothing else: no ``__getattr__`` passthrough, no access to the wrapped client.
tests/test_readonly.py AST-scans the package to make sure nobody sneaks
another SDK call in elsewhere.
"""

from __future__ import annotations

from typing import Any

import dropbox
from dropbox.files import ListFolderResult
from dropbox.users import FullAccount

from dropbox_organizer.auth import SCOPES

ALLOWED_METHODS: frozenset[str] = frozenset(
    {"files_list_folder", "files_list_folder_continue", "users_get_current_account"}
)

USER_AGENT = "dropbox-organizer-readonly/0.2"


class ReadOnlyDropbox:
    __slots__ = ("_dbx",)

    def __init__(self, sdk_client: Any) -> None:
        self._dbx = sdk_client

    @classmethod
    def from_refresh_token(cls, refresh_token: str, app_key: str) -> ReadOnlyDropbox:
        sdk = dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            scope=list(SCOPES),
            # We handle RateLimitError ourselves (logged, honours retry_after).
            max_retries_on_rate_limit=0,
            user_agent=USER_AGENT,
        )
        return cls(sdk)

    def files_list_folder(
        self, path: str, *, recursive: bool, include_deleted: bool, limit: int
    ) -> ListFolderResult:
        return self._dbx.files_list_folder(
            path, recursive=recursive, include_deleted=include_deleted, limit=limit
        )

    def files_list_folder_continue(self, cursor: str) -> ListFolderResult:
        return self._dbx.files_list_folder_continue(cursor)

    def users_get_current_account(self) -> FullAccount:
        return self._dbx.users_get_current_account()
