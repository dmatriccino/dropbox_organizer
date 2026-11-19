"""One-time PKCE (no redirect) OAuth login requesting read-only scopes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dropbox import DropboxOAuth2FlowNoRedirect

# Exactly these, nothing else. README documents the same list.
SCOPES: tuple[str, ...] = ("files.metadata.read", "account_info.read")


class AuthError(RuntimeError):
    pass


def run_auth_flow(
    app_key: str,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    flow_factory: Callable[..., Any] = DropboxOAuth2FlowNoRedirect,
) -> str:
    """Run the interactive PKCE flow and return the refresh token."""
    flow = flow_factory(
        app_key,
        use_pkce=True,
        token_access_type="offline",
        scope=list(SCOPES),
    )
    url = flow.start()
    print_fn("1. Open this URL in a browser:\n\n   " + url + "\n")
    print_fn('2. Click "Allow" (log in first if needed).')
    print_fn("3. Copy the authorization code Dropbox shows you.")
    code = input_fn("Paste the authorization code here: ").strip()
    if not code:
        raise AuthError("No authorization code entered.")
    result = flow.finish(code)

    granted = set((result.scope or "").split())
    extra = granted - set(SCOPES)
    if extra:
        raise AuthError(
            "Dropbox granted scopes beyond the read-only set: "
            + ", ".join(sorted(extra))
            + ". Refusing to save this token; fix the app's permissions."
        )
    if not result.refresh_token:
        raise AuthError("Dropbox did not return a refresh token.")
    return result.refresh_token
