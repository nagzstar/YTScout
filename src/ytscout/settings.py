"""Settings loader.

Two sources, never printed together:

* ``config/settings.yaml`` (the user's copy of ``config/settings.example.yaml``): tunables.
* ``.env`` at the repo root: the four ``YT_*`` secrets and paths. A real environment
  variable with the same name wins over the file.

The repo root is the folder holding ``.env`` (and ``pyproject.toml``). It is found by
walking up from the current directory; relative paths in either file resolve against it,
not against the cwd. ``Settings`` exposes the secret file paths and never opens them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

SETTINGS_RELPATH = Path("config") / "settings.yaml"
EXAMPLE_RELPATH = Path("config") / "settings.example.yaml"
ENV_FILENAME = ".env"
ROOT_MARKERS = ("pyproject.toml", ENV_FILENAME)

DEFAULT_DATA_DIR = "data"
DEFAULT_USD_GBP = 0.78
DEFAULT_PIPELINE_REPO_PATH = r"C:\Users\nagaj\git\top-five-animals-1"
DEFAULT_SHORTS_PER_MONTH = 20
DEFAULT_LONGFORM_PER_MONTH = 4
DEFAULT_QUOTA_DAILY_CAP = 9000
DEFAULT_CLIENT_SECRET_PATH = "scripts/.secrets/client_secret.json"
DEFAULT_TOKEN_PATH = "scripts/.secrets/token.json"


class SettingsError(Exception):
    """The settings could not be loaded or are invalid."""


class SettingsMissing(SettingsError):
    """``config/settings.yaml`` does not exist."""

    def __init__(self, path: Path, example: Path) -> None:
        self.path = path
        self.example = example
        super().__init__(
            f"settings file not found: {path}\ncopy {example} to {path} and fill in own_channel_id"
        )


@dataclass(frozen=True)
class VideosPerMonth:
    shorts: int = DEFAULT_SHORTS_PER_MONTH
    longform: int = DEFAULT_LONGFORM_PER_MONTH


@dataclass(frozen=True)
class Quota:
    daily_cap: int = DEFAULT_QUOTA_DAILY_CAP


@dataclass(frozen=True)
class ClaudeSettings:
    model: str | None = None  # None = Claude Code's default model


@dataclass(frozen=True)
class Settings:
    """Everything the tool is configured with. ``api_key`` is excluded from ``repr``."""

    repo_root: Path
    settings_path: Path
    own_channel_id: str
    data_dir: Path
    usd_gbp: float
    pipeline_repo_path: Path
    videos_per_month: VideosPerMonth
    quota: Quota
    claude: ClaudeSettings
    client_secret_path: Path
    token_path: Path
    api_key: str | None = field(default=None, repr=False)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) to the first folder holding a root marker.

    Falls back to ``start`` itself when no marker is found, so a directory with no
    ``config/settings.yaml`` simply reports the settings as missing.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    return here


def _read_env(repo_root: Path) -> dict[str, str]:
    """``YT_*`` values: real environment first, then ``.env``. Empty strings count as unset."""
    file_values: dict[str, str | None] = {}
    env_path = repo_root / ENV_FILENAME
    if env_path.is_file():
        file_values = dotenv_values(env_path)
    merged: dict[str, str] = {}
    for key in ("YT_API_KEY", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH", "YT_CHANNEL_ID"):
        value = os.environ.get(key)
        if not value:
            value = file_values.get(key)
        if value:
            merged[key] = value.strip()
    return merged


def _resolve(repo_root: Path, raw: str | os.PathLike[str]) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else repo_root / path


def _section(doc: dict[str, Any], key: str) -> dict[str, Any]:
    value = doc.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SettingsError(f"settings key {key!r} must be a mapping")
    return value


def load(path: Path | str | None = None, *, repo_root: Path | str | None = None) -> Settings:
    """Load settings from ``path`` (default ``<repo_root>/config/settings.yaml``).

    Raises ``SettingsMissing`` if the file is absent and ``SettingsError`` if a required
    key is missing or malformed.
    """
    root = Path(repo_root).resolve() if repo_root is not None else find_repo_root()
    settings_path = _resolve(root, path) if path is not None else root / SETTINGS_RELPATH
    if not settings_path.is_file():
        raise SettingsMissing(settings_path, root / EXAMPLE_RELPATH)

    with settings_path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise SettingsError(f"{settings_path} must contain a YAML mapping at the top level")

    env = _read_env(root)

    own_channel_id = str(doc.get("own_channel_id") or env.get("YT_CHANNEL_ID") or "").strip()
    if not own_channel_id:
        raise SettingsError(
            "own_channel_id is required: set it in config/settings.yaml or YT_CHANNEL_ID in .env"
        )

    videos = _section(doc, "videos_per_month")
    quota = _section(doc, "quota")
    claude = _section(doc, "claude")

    try:
        usd_gbp = float(doc.get("usd_gbp", DEFAULT_USD_GBP))
        videos_per_month = VideosPerMonth(
            shorts=int(videos.get("shorts", DEFAULT_SHORTS_PER_MONTH)),
            longform=int(videos.get("longform", DEFAULT_LONGFORM_PER_MONTH)),
        )
        quota_settings = Quota(daily_cap=int(quota.get("daily_cap", DEFAULT_QUOTA_DAILY_CAP)))
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"malformed numeric value in {settings_path}: {exc}") from exc

    model = claude.get("model")
    claude_settings = ClaudeSettings(model=str(model) if model else None)

    return Settings(
        repo_root=root,
        settings_path=settings_path,
        own_channel_id=own_channel_id,
        data_dir=_resolve(root, str(doc.get("data_dir") or DEFAULT_DATA_DIR)),
        usd_gbp=usd_gbp,
        pipeline_repo_path=_resolve(
            root, str(doc.get("pipeline_repo_path") or DEFAULT_PIPELINE_REPO_PATH)
        ),
        videos_per_month=videos_per_month,
        quota=quota_settings,
        claude=claude_settings,
        client_secret_path=_resolve(
            root, env.get("YT_CLIENT_SECRET_PATH", DEFAULT_CLIENT_SECRET_PATH)
        ),
        token_path=_resolve(root, env.get("YT_TOKEN_PATH", DEFAULT_TOKEN_PATH)),
        api_key=env.get("YT_API_KEY"),
    )
