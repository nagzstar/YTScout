"""OAuth for the YouTube Analytics API: load, refresh and check the own channel's token.

The token file lives at ``Settings.token_path`` (``YT_TOKEN_PATH``). Nothing here prints
or logs a token value; ``check_scopes`` and ``describe`` deal in scope names only.
``run_consent_flow`` opens a browser for Google's consent screen, so only ``ytscout auth``
(an Active step, blocked in unattended sessions) ever calls it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
SCOPES: tuple[str, ...] = (ANALYTICS_SCOPE, MONETARY_SCOPE)


class TokenError(Exception):
    """The token file exists but cannot be loaded or refreshed."""


def _save(credentials: Any, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")


def load_credentials(token_path: Path, *, refresh: bool = True) -> Any | None:
    """The token at ``token_path`` as ``google.oauth2.credentials.Credentials``.

    ``None`` when the file is absent. With ``refresh``, an expired token that has a refresh
    token is refreshed and written back. Raises ``TokenError`` when the file will not parse
    or the refresh fails; the message never contains token contents.
    """
    if not token_path.is_file():
        return None
    from google.oauth2.credentials import Credentials

    try:
        credentials = Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, KeyError, TypeError) as exc:
        # The library names the missing field; that is safe to show, values are not.
        raise TokenError(f"token file will not load as an authorized-user token: {exc}") from exc
    if refresh and credentials.expired and credentials.refresh_token:
        refresh_token(credentials, token_path)
    return credentials


def refresh_token(credentials: Any, token_path: Path) -> None:
    """Refresh ``credentials`` against Google and write them back to ``token_path``."""
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request

    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        raise TokenError("token refresh was refused by Google (revoked or expired)") from exc
    except TransportError as exc:
        raise TokenError(f"token refresh could not reach Google: {type(exc).__name__}") from exc
    _save(credentials, token_path)


def run_consent_flow(client_secret_path: Path, token_path: Path) -> Any:
    """Open Google's consent screen for the two read-only Analytics scopes; save the token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), list(SCOPES))
    credentials = flow.run_local_server(port=0)
    _save(credentials, token_path)
    return credentials


def granted_scopes(credentials: Any) -> list[str]:
    """The scopes the token carries: what Google granted if known, else what it recorded."""
    scopes: Iterable[str] | None = getattr(credentials, "granted_scopes", None) or getattr(
        credentials, "scopes", None
    )
    return sorted(set(scopes or ()))


def check_scopes(credentials: Any) -> list[str]:
    """Problems with the token's scopes; empty means fit for ``collect --analytics``.

    The monetary scope must be present, and every scope must be ``*.readonly``: this is a
    read-only tool, so a write scope such as ``youtube.upload`` has no place in its token.
    """
    scopes = granted_scopes(credentials)
    problems: list[str] = []
    if MONETARY_SCOPE not in scopes:
        problems.append(f"missing scope {MONETARY_SCOPE}")
    for scope in scopes:
        if not scope.endswith(".readonly"):
            problems.append(f"not read-only: {scope}")
    return problems


@dataclass
class TokenStatus:
    """What ``ytscout auth --status`` reports. Holds scope names, never token values."""

    present: bool
    loaded: bool = False
    expired: bool = False
    has_refresh_token: bool = False
    # None: not needed (token still valid) or impossible (no refresh token).
    refreshed: bool | None = None
    refresh_error: str | None = None
    scopes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    error: str | None = None


def describe(token_path: Path) -> TokenStatus:
    """Load the token, refresh it if expired, and report on it without raising."""
    try:
        credentials = load_credentials(token_path, refresh=False)
    except TokenError as exc:
        return TokenStatus(present=True, error=str(exc))
    if credentials is None:
        return TokenStatus(present=False)
    status = TokenStatus(
        present=True,
        loaded=True,
        expired=bool(credentials.expired),
        has_refresh_token=bool(credentials.refresh_token),
        scopes=granted_scopes(credentials),
        problems=check_scopes(credentials),
    )
    if status.expired and status.has_refresh_token:
        try:
            refresh_token(credentials, token_path)
            status.refreshed = True
            status.scopes = granted_scopes(credentials)
            status.problems = check_scopes(credentials)
        except TokenError as exc:
            status.refreshed = False
            status.refresh_error = str(exc)
    return status
