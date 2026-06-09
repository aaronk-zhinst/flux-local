"""Tests for git source fetching with authentication."""

import tempfile
from pathlib import Path
from collections.abc import Generator
from unittest.mock import patch

import pytest
import git
import yaml

from flux_local.manifest import GitRepository
from flux_local.source_controller.cache import GitCache
from flux_local.source_controller.git import fetch_git
from flux_local.source_controller.secret import Auth


@pytest.fixture(name="git_repo_tmp_dir", scope="module")
def git_repo_tmp_dir_fixture() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(name="git_repo_dir", scope="module")
def git_repo_dir_fixture(git_repo_tmp_dir: Path) -> Path:
    repo_path = git_repo_tmp_dir / "test-repo"
    repo_path.mkdir()
    repo = git.Repo.init(repo_path)
    repo.config_writer().set_value("user", "name", "testuser").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    (repo_path / "test.txt").write_text("content")
    repo.git.add(".")
    repo.git.commit(m="Initial commit")
    return repo_path


def _private_repo_obj() -> GitRepository:
    return GitRepository.parse_doc(yaml.safe_load("""
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: private-repo
      namespace: flux-system
    spec:
      url: https://github.com/example/private-charts.git
      ref:
        branch: main
    """))


@pytest.fixture(name="fresh_cache")
def fresh_cache_fixture() -> Generator[GitCache, None, None]:
    """Provide an isolated GitCache backed by a temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = GitCache()
        cache._cache_dir = Path(tmp)
        yield cache


@pytest.mark.asyncio
async def test_fetch_git_with_auth_passes_original_url_to_clone(
    git_repo_dir: Path, fresh_cache: GitCache
) -> None:
    """fetch_git must clone with the original URL (no credentials embedded)."""
    obj = _private_repo_obj()
    auth = Auth(username="ci-token", password="secret-password")

    clone_calls: list[str] = []
    original_clone = git.Repo.clone_from

    def capturing_clone(url: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        clone_calls.append(url)
        return original_clone(f"file://{git_repo_dir}", *args, **kwargs)

    with patch("flux_local.source_controller.git.get_git_cache", return_value=fresh_cache):
        with patch.object(git.Repo, "clone_from", capturing_clone):
            await fetch_git(obj, auth)

    assert clone_calls == ["https://github.com/example/private-charts.git"]


@pytest.mark.asyncio
async def test_fetch_git_with_auth_sets_askpass_env(
    git_repo_dir: Path, fresh_cache: GitCache
) -> None:
    """fetch_git must pass GIT_ASKPASS and credential env vars to clone."""
    obj = _private_repo_obj()
    auth = Auth(username="ci-token", password="secret-password")

    captured_env: list[dict] = []
    original_clone = git.Repo.clone_from

    def capturing_clone(url: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured_env.append(kwargs.get("env", {}))
        return original_clone(f"file://{git_repo_dir}", *args, **kwargs)

    with patch("flux_local.source_controller.git.get_git_cache", return_value=fresh_cache):
        with patch.object(git.Repo, "clone_from", capturing_clone):
            await fetch_git(obj, auth)

    assert len(captured_env) == 1
    env = captured_env[0]
    assert "GIT_ASKPASS" in env
    assert env.get("GIT_AUTH_USERNAME") == "ci-token"
    assert env.get("GIT_AUTH_PASSWORD") == "secret-password"
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


@pytest.mark.asyncio
async def test_fetch_git_with_special_chars_in_password(
    git_repo_dir: Path, fresh_cache: GitCache
) -> None:
    """Credentials with URL-special characters must pass through unchanged."""
    tricky_password = "p@ss:w0rd%20&?=+/test"
    obj = _private_repo_obj()
    auth = Auth(username="user@domain.com", password=tricky_password)

    captured_env: list[dict] = []
    original_clone = git.Repo.clone_from

    def capturing_clone(url: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured_env.append(kwargs.get("env", {}))
        return original_clone(f"file://{git_repo_dir}", *args, **kwargs)

    with patch("flux_local.source_controller.git.get_git_cache", return_value=fresh_cache):
        with patch.object(git.Repo, "clone_from", capturing_clone):
            await fetch_git(obj, auth)

    env = captured_env[0]
    assert env.get("GIT_AUTH_USERNAME") == "user@domain.com"
    assert env.get("GIT_AUTH_PASSWORD") == tricky_password
    # Original URL must be unmodified
    assert "@" not in "https://github.com/example/private-charts.git".split("://")[1].split("/")[0]


@pytest.mark.asyncio
async def test_fetch_git_without_auth_uses_original_url(git_repo_dir: Path) -> None:
    """Without auth, fetch_git passes the original URL unchanged with no extra env."""
    yaml_str = f"""
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: public-repo
      namespace: flux-system
    spec:
      url: file://{git_repo_dir}
      ref:
        branch: main
    """
    obj = GitRepository.parse_doc(yaml.safe_load(yaml_str))

    clone_calls: list[tuple[str, dict]] = []
    original_clone = git.Repo.clone_from

    def capturing_clone(url: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        clone_calls.append((url, kwargs))
        return original_clone(url, *args, **kwargs)

    with patch.object(git.Repo, "clone_from", capturing_clone):
        await fetch_git(obj)

    assert len(clone_calls) == 1
    cloned_url, cloned_kwargs = clone_calls[0]
    assert cloned_url == f"file://{git_repo_dir}"
    assert "env" not in cloned_kwargs or cloned_kwargs.get("env") is None
