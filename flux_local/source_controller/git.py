"""Git repository controller."""

import contextlib
import git
import logging
import os
import stat
import sys
import tempfile
from collections.abc import Generator

from flux_local.manifest import GitRepository

from .artifact import GitArtifact
from .cache import get_git_cache
from .secret import Auth

_LOGGER = logging.getLogger(__name__)


class GitError(Exception):
    """Exception raised for git operations."""


@contextlib.contextmanager
def _git_credentials_env(auth: Auth) -> Generator[dict[str, str], None, None]:
    """Context manager that provides GIT_ASKPASS env vars for credential injection.

    Credentials are passed to git via a temporary ASKPASS helper script and
    environment variables — they never appear in URLs or .git/config.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="flux-local-askpass-"
    ) as f:
        askpass_path = f.name
        f.write(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "if 'Username' in prompt or 'username' in prompt:\n"
            "    print(os.environ.get('GIT_AUTH_USERNAME', ''))\n"
            "else:\n"
            "    print(os.environ.get('GIT_AUTH_PASSWORD', ''))\n"
        )
    os.chmod(askpass_path, stat.S_IRWXU)
    try:
        yield {
            "GIT_ASKPASS": askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTH_USERNAME": auth.username,
            "GIT_AUTH_PASSWORD": auth.password,
        }
    finally:
        os.unlink(askpass_path)


async def fetch_git(obj: GitRepository, auth: Auth | None = None) -> GitArtifact:
    """Fetch a Git repository using the cache.

    Args:
        obj: The GitRepository object containing repository details
        auth: Optional authentication credentials for private repositories

    Returns:
        GitArtifact: Artifact containing the local path and URL

    Raises:
        GitError: If git operations fail
    """
    try:
        cache = get_git_cache()
        repo_path = cache.get_repo_path(obj.url, obj.ref.ref_str if obj.ref else None)

        with contextlib.ExitStack() as stack:
            env: dict[str, str] | None = None
            if auth:
                env = stack.enter_context(_git_credentials_env(auth))

            if (repo_path / ".git").exists():
                _LOGGER.info("Updating existing repository at %s", repo_path)
                repo = git.Repo(str(repo_path))
                if env:
                    with repo.git.custom_environment(**env):
                        repo.git.pull()
                else:
                    repo.git.pull()
            else:
                _LOGGER.info("Cloning repository %s to %s", obj.url, repo_path)
                repo = git.Repo.clone_from(
                    obj.url, str(repo_path), env=env if env else None
                )

        # Handle reference checkout based on priority: commit > tag > semver > branch
        if obj.ref:
            if obj.ref.commit:
                _LOGGER.info("Checking out commit %s", obj.ref.commit)
                repo.git.checkout(obj.ref.commit)
            elif obj.ref.tag:
                _LOGGER.info("Checking out tag %s", obj.ref.tag)
                try:
                    repo.git.checkout(obj.ref.tag)
                except git.exc.GitCommandError:
                    repo.git.fetch("--tags")
                    repo.git.checkout(obj.ref.tag)
            elif obj.ref.semver:
                _LOGGER.info("Checking out semver %s", obj.ref.semver)
                raise NotImplementedError("Semver tag filtering not implemented")
            elif obj.ref.branch:
                _LOGGER.info("Checking out branch %s", obj.ref.branch)
                repo.git.checkout(obj.ref.branch)

        return GitArtifact(
            url=obj.url,
            local_path=str(repo_path),
            ref=obj.ref,
        )

    except git.exc.GitCommandError as e:
        raise GitError(f"Git operation failed: {e}") from e
    except Exception as e:
        raise GitError(f"Failed to fetch repository: {e}") from e
