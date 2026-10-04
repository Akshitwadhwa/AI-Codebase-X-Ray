from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote


class RepositoryError(ValueError):
    """An expected, user-correctable repository input/download error."""


GITHUB_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?(?:[?#].*)?$", re.I)


def parse_repository_url(value: str) -> tuple[str, str]:
    match = GITHUB_URL.match(value.strip())
    if not match:
        raise RepositoryError("Enter a public GitHub URL such as https://github.com/owner/repository")
    owner, repo = match.groups()
    if owner.lower() in {"features", "topics", "settings"}:
        raise RepositoryError("That does not look like a repository URL")
    return owner, repo


def _request_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "AI-Codebase-X-Ray/phase-1"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RepositoryError("Repository was not found or is private") from exc
        if exc.code == 403:
            raise RepositoryError("GitHub rate limit reached. Add GITHUB_TOKEN to .env and try again") from exc
        raise RepositoryError(f"GitHub returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RepositoryError("Could not reach GitHub. Check your network connection") from exc


def download_repository(owner: str, repo: str, destination: Path) -> str:
    # Clone only inside the backend's temporary workspace. The browser/device
    # never receives a checkout. A shallow clone is the most reliable network
    # path on this machine; archive download is retained as a fallback.
    clone_root = destination / "source"
    clone_target = clone_root / repo
    try:
        clone_root.mkdir()
        subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{owner}/{repo}.git", str(clone_target)],
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
        )
        branch_result = subprocess.run(["git", "-C", str(clone_target), "branch", "--show-current"], check=False, capture_output=True, text=True)
        return branch_result.stdout.strip() or "default"
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        # Fall back to codeload when Git is unavailable or a shallow clone is
        # rejected. This also keeps the demo usable in restricted environments.
        shutil.rmtree(clone_root, ignore_errors=True)

    # The metadata endpoint is useful for discovering the default branch, but
    # public unauthenticated API calls are rate-limited. If that limit is hit,
    # try the two conventional branch names so the demo still works without a
    # token for most repositories.
    try:
        metadata = _request_json(f"https://api.github.com/repos/{owner}/{repo}")
        branches = [metadata.get("default_branch") or "main"]
    except RepositoryError as exc:
        if "rate limit" not in str(exc).lower():
            raise
        branches = ["main", "master"]
    headers = {"User-Agent": "AI-Codebase-X-Ray/phase-1"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    max_bytes = int(os.getenv("MAX_ARCHIVE_BYTES", "50000000"))
    archive = destination / "repository.zip"
    last_error: Exception | None = None
    for branch in branches:
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{quote(branch, safe='')}"
        request = urllib.request.Request(archive_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise RepositoryError("Repository archive is too large for this demo (limit: 50 MB)")
                    output.write(chunk)
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 404 or branch == branches[-1]:
                raise RepositoryError(f"Could not download the repository archive (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RepositoryError("Could not download the repository archive") from exc
    else:
        raise RepositoryError("Could not find a main or master branch in this repository") from last_error

    extract_root = destination / "source"
    extract_root.mkdir()
    try:
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                target = (extract_root / member.filename).resolve()
                if extract_root.resolve() not in target.parents and target != extract_root.resolve():
                    raise RepositoryError("Repository archive contains an unsafe path")
            zipped.extractall(extract_root)
    except zipfile.BadZipFile as exc:
        raise RepositoryError("GitHub returned an invalid repository archive") from exc
    roots = [item for item in extract_root.iterdir() if item.is_dir()]
    if not roots:
        raise RepositoryError("The repository archive did not contain source files")
    return branch
