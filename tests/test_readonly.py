"""Read-only guarantees, enforced statically.

1. No Dropbox SDK API method outside the allowlist is called anywhere in the
   package, whatever the receiver is called.
2. No method outside the allowlist is called on anything named like a
   Dropbox client (dbx, client, _dbx, sdk, ...), including via getattr().
3. ReadOnlyDropbox's public surface is exactly the allowlist.
4. The OAuth flow asks for exactly the two read-only scopes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar

import dropbox
import pytest
from dropbox.base import DropboxBase

import dropbox_organizer
from dropbox_organizer.auth import SCOPES, AuthError, run_auth_flow
from dropbox_organizer.client import ALLOWED_METHODS, ReadOnlyDropbox

PACKAGE_DIR = Path(dropbox_organizer.__file__).parent

# Every Dropbox API route method the SDK knows about (files_delete_v2, sharing_*, ...).
SDK_ROUTE_METHODS = frozenset(n for n in vars(DropboxBase) if not n.startswith("_"))
# Transport-level escape hatches that could hit arbitrary endpoints.
SDK_TRANSPORT_METHODS = frozenset(
    n for n in vars(dropbox.dropbox_client._DropboxTransport) if not n.startswith("_")
)
CLIENT_NAME = re.compile(r"(dbx|client|sdk)$", re.IGNORECASE)


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_violations(source: str, filename: str = "<string>") -> list[str]:
    tree = ast.parse(source, filename)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        where = f"{filename}:{node.lineno}"
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in ALLOWED_METHODS:
                continue
            if attr in SDK_ROUTE_METHODS or attr in SDK_TRANSPORT_METHODS - {"close"}:
                problems.append(f"{where}: forbidden Dropbox call .{attr}()")
                continue
            recv = _receiver_name(func.value)
            if recv and CLIENT_NAME.search(recv):
                problems.append(f"{where}: {recv}.{attr}() is not in the read-only allowlist")
        elif isinstance(func, ast.Name) and func.id == "getattr" and node.args:
            recv = _receiver_name(node.args[0])
            if recv and CLIENT_NAME.search(recv):
                problems.append(f"{where}: dynamic getattr() on {recv} is not allowed")
    return problems


def package_sources() -> list[Path]:
    files = sorted(PACKAGE_DIR.rglob("*.py"))
    assert files, "found no package sources to scan"
    return files


def test_allowlist_is_exactly_three_read_methods():
    assert {
        "files_list_folder",
        "files_list_folder_continue",
        "users_get_current_account",
    } == ALLOWED_METHODS
    # Sanity: they really are SDK route methods, so the scanner can see them.
    assert ALLOWED_METHODS <= SDK_ROUTE_METHODS


def test_package_makes_no_forbidden_dropbox_calls():
    problems: list[str] = []
    for path in package_sources():
        problems += find_violations(path.read_text(), str(path.relative_to(PACKAGE_DIR.parent)))
    assert problems == []


def _constructs_sdk_client(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            name = _receiver_name(node.func)
            if name in {"Dropbox", "DropboxTeam"}:
                return True
    return False


def test_the_dropbox_client_is_only_built_in_client_py():
    builders = [p.name for p in package_sources() if _constructs_sdk_client(p)]
    assert builders == ["client.py"]


@pytest.mark.parametrize(
    "snippet",
    [
        "dbx.files_delete_v2('/a')",
        "dbx.files_delete_batch([])",
        "client.files_move_v2('/a', '/b')",
        "self._dbx.files_download('/a')",
        "dbx.files_download_to_file('x', '/a')",
        "anything.files_upload(b'', '/a')",
        "x.sharing_create_shared_link_with_settings('/a')",
        "dbx.files_permanently_delete('/a')",
        "dbx.request(route, 'files', {}, None)",
        "self.client.some_new_method()",
        "getattr(dbx, 'files_' + 'delete_v2')('/a')",
        "dbx.files_get_metadata('/a')",  # read-only but not allowlisted
    ],
)
def test_scanner_catches_violations(snippet):
    assert find_violations(snippet), f"scanner missed: {snippet}"


@pytest.mark.parametrize(
    "snippet",
    [
        "dbx.files_list_folder('', recursive=True, include_deleted=False, limit=2000)",
        "self._dbx.files_list_folder_continue(cursor)",
        "client.users_get_current_account()",
        "conn.execute('DELETE FROM media_files')",  # local SQLite is fine
    ],
)
def test_scanner_allows_read_only_calls(snippet):
    assert find_violations(snippet) == []


def test_readonly_wrapper_surface_is_the_allowlist():
    public = {n for n in dir(ReadOnlyDropbox) if not n.startswith("_")}
    assert public - {"from_refresh_token"} == ALLOWED_METHODS


def test_readonly_wrapper_has_no_passthrough():
    ro = ReadOnlyDropbox(object())
    for name in ("files_delete_v2", "files_move_v2", "files_upload", "files_download"):
        with pytest.raises(AttributeError):
            getattr(ro, name)
    with pytest.raises(AttributeError):
        ro.extra = 1  # __slots__: can't bolt anything on


def test_from_refresh_token_requests_only_read_scopes(monkeypatch):
    captured = {}

    def fake_dropbox(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(dropbox, "Dropbox", fake_dropbox)
    ReadOnlyDropbox.from_refresh_token("rt", "key")
    assert captured["scope"] == ["files.metadata.read", "account_info.read"]
    assert captured["max_retries_on_rate_limit"] == 0


class FakeFlow:
    instances: ClassVar[list[FakeFlow]] = []

    def __init__(self, app_key, **kwargs):
        self.app_key = app_key
        self.kwargs = kwargs
        self.granted = "account_info.read files.metadata.read"
        FakeFlow.instances.append(self)

    def start(self):
        return "https://www.dropbox.com/oauth2/authorize?fake"

    def finish(self, code):
        return dropbox.oauth.OAuth2FlowNoRedirectResult(
            "at", "acct", "uid", "refresh-token", None, self.granted
        )


def test_auth_flow_requests_exactly_read_only_scopes():
    FakeFlow.instances.clear()
    token = run_auth_flow(
        "key", input_fn=lambda _: "code", print_fn=lambda _: None, flow_factory=FakeFlow
    )
    flow = FakeFlow.instances[0]
    assert token == "refresh-token"
    assert SCOPES == ("files.metadata.read", "account_info.read")
    assert flow.kwargs == {
        "use_pkce": True,
        "token_access_type": "offline",
        "scope": ["files.metadata.read", "account_info.read"],
    }


def test_auth_refuses_token_with_extra_scopes():
    class GreedyFlow(FakeFlow):
        def finish(self, code):
            self.granted = "files.metadata.read account_info.read files.content.write"
            return super().finish(code)

    with pytest.raises(AuthError, match=r"files\.content\.write"):
        run_auth_flow(
            "key", input_fn=lambda _: "c", print_fn=lambda _: None, flow_factory=GreedyFlow
        )
