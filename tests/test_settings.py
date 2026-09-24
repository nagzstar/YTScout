"""Settings loader: defaults, .env precedence, repo-root resolution, fallbacks, errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytscout import settings as s

MINIMAL = "own_channel_id: UCabc\n"

YT_KEYS = ("YT_API_KEY", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH", "YT_CHANNEL_ID")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's real environment must not leak into these tests."""
    for key in YT_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    return tmp_path


def write_settings(repo: Path, text: str = MINIMAL) -> Path:
    path = repo / "config" / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def write_env(repo: Path, **values: str) -> Path:
    path = repo / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def test_defaults_apply(repo: Path) -> None:
    write_settings(repo)
    cfg = s.load(repo_root=repo)
    assert cfg.own_channel_id == "UCabc"
    assert cfg.data_dir == repo / "data"
    assert cfg.usd_gbp == pytest.approx(0.78)
    assert cfg.pipeline_repo_path == Path(r"C:\Users\nagaj\git\top-five-animals-1")
    assert cfg.videos_per_month.shorts == 20
    assert cfg.videos_per_month.longform == 4
    assert cfg.quota.daily_cap == 9000
    assert cfg.claude.model is None
    assert cfg.api_key is None
    assert cfg.has_api_key is False
    assert cfg.client_secret_path == repo / "scripts" / ".secrets" / "client_secret.json"
    assert cfg.token_path == repo / "scripts" / ".secrets" / "token.json"
    assert cfg.settings_path == repo / "config" / "settings.yaml"
    assert cfg.repo_root == repo


def test_yaml_values_override_defaults(repo: Path) -> None:
    write_settings(
        repo,
        "own_channel_id: UCabc\n"
        "data_dir: elsewhere\n"
        "usd_gbp: 0.8\n"
        "pipeline_repo_path: pipeline\n"
        "videos_per_month: {shorts: 30, longform: 2}\n"
        "quota: {daily_cap: 5000}\n"
        "claude: {model: claude-sonnet-5}\n",
    )
    cfg = s.load(repo_root=repo)
    assert cfg.data_dir == repo / "elsewhere"
    assert cfg.usd_gbp == pytest.approx(0.8)
    assert cfg.pipeline_repo_path == repo / "pipeline"
    assert cfg.videos_per_month == s.VideosPerMonth(shorts=30, longform=2)
    assert cfg.quota.daily_cap == 5000
    assert cfg.claude.model == "claude-sonnet-5"


def test_dotenv_values_are_picked_up(repo: Path) -> None:
    write_settings(repo)
    write_env(
        repo,
        YT_API_KEY="from-file",
        YT_CLIENT_SECRET_PATH="secrets/cs.json",
        YT_TOKEN_PATH="secrets/tok.json",
    )
    cfg = s.load(repo_root=repo)
    assert cfg.api_key == "from-file"
    assert cfg.has_api_key is True
    assert cfg.client_secret_path == repo / "secrets" / "cs.json"
    assert cfg.token_path == repo / "secrets" / "tok.json"


def test_real_environment_variable_wins_over_dotenv(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(repo)
    write_env(repo, YT_API_KEY="from-file", YT_TOKEN_PATH="file/tok.json")
    monkeypatch.setenv("YT_API_KEY", "from-env")
    monkeypatch.setenv("YT_TOKEN_PATH", "env/tok.json")
    cfg = s.load(repo_root=repo)
    assert cfg.api_key == "from-env"
    assert cfg.token_path == repo / "env" / "tok.json"


def test_empty_dotenv_value_counts_as_unset(repo: Path) -> None:
    write_settings(repo)
    write_env(repo, YT_API_KEY="", YT_TOKEN_PATH="")
    cfg = s.load(repo_root=repo)
    assert cfg.api_key is None
    assert cfg.token_path == repo / "scripts" / ".secrets" / "token.json"


def test_relative_secret_paths_resolve_against_repo_root_not_cwd(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(repo, "own_channel_id: UCabc\ndata_dir: d\n")
    write_env(repo, YT_CLIENT_SECRET_PATH="rel/cs.json", YT_TOKEN_PATH="rel/tok.json")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    cfg = s.load(repo_root=repo)
    assert cfg.client_secret_path == repo / "rel" / "cs.json"
    assert cfg.token_path == repo / "rel" / "tok.json"
    assert cfg.data_dir == repo / "d"
    assert not str(cfg.token_path).startswith(str(elsewhere))


def test_absolute_secret_paths_are_kept(repo: Path, tmp_path: Path) -> None:
    write_settings(repo)
    absolute = tmp_path / "abs" / "tok.json"
    write_env(repo, YT_TOKEN_PATH=str(absolute))
    cfg = s.load(repo_root=repo)
    assert cfg.token_path == absolute


def test_repo_root_is_found_by_walking_up_from_cwd(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(repo)
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert s.find_repo_root() == repo
    assert s.load().repo_root == repo


def test_repo_root_falls_back_to_cwd_without_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert s.find_repo_root() == tmp_path.resolve()


def test_own_channel_id_falls_back_to_yt_channel_id(repo: Path) -> None:
    write_settings(repo, "own_channel_id: ''\n")
    write_env(repo, YT_CHANNEL_ID="UCfromenv")
    assert s.load(repo_root=repo).own_channel_id == "UCfromenv"


def test_own_channel_id_in_yaml_beats_yt_channel_id(repo: Path) -> None:
    write_settings(repo, "own_channel_id: UCyaml\n")
    write_env(repo, YT_CHANNEL_ID="UCfromenv")
    assert s.load(repo_root=repo).own_channel_id == "UCyaml"


def test_missing_own_channel_id_everywhere_raises(repo: Path) -> None:
    write_settings(repo, "usd_gbp: 0.5\n")
    with pytest.raises(s.SettingsError, match="own_channel_id"):
        s.load(repo_root=repo)


def test_missing_settings_file_raises_and_names_the_example(repo: Path) -> None:
    with pytest.raises(s.SettingsMissing) as excinfo:
        s.load(repo_root=repo)
    assert "settings.example.yaml" in str(excinfo.value)
    assert excinfo.value.path == repo / "config" / "settings.yaml"


def test_explicit_path_argument(repo: Path, tmp_path: Path) -> None:
    custom = tmp_path / "custom.yaml"
    custom.write_text(MINIMAL, encoding="utf-8")
    cfg = s.load(custom, repo_root=repo)
    assert cfg.settings_path == custom
    assert cfg.data_dir == repo / "data"


def test_malformed_section_raises(repo: Path) -> None:
    write_settings(repo, "own_channel_id: UCabc\nquota: 12\n")
    with pytest.raises(s.SettingsError, match="quota"):
        s.load(repo_root=repo)


def test_api_key_is_not_in_repr(repo: Path) -> None:
    write_settings(repo)
    write_env(repo, YT_API_KEY="super-secret-value")
    cfg = s.load(repo_root=repo)
    assert "super-secret-value" not in repr(cfg)
    assert "super-secret-value" not in str(cfg)
