from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

from ..github import RepositoryError, download_repository, parse_repository_url
from ..models import (
    CiReport,
    CiRun,
    DiffFile,
    DiffResult,
    FileAtRef,
    GitCheckResult,
    PullRequestInfo,
    PytestResult,
)

# The gateway calls this service for every Git or GitHub operation.
# Scan, graph, retrieval, and the model providers do not call Git themselves.

_PR_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+?)/pull/(\d+)/?(?:[?#].*)?$", re.I)
_TREE_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+?)/tree/([^?#]+)/?(?:[?#].*)?$", re.I)
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_READABLE_STATUS = {"added", "modified", "changed", "renamed"}

CLONE_TIMEOUT_SECONDS = 60
PYTEST_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 100_000
MAX_PATCH_CHARS = 4_000
MAX_FILE_CHARS = 20_000
MAX_FILE_BYTES = 100_000
MAX_CHECK_FILES = 3
MAX_DIFF_FILES = 40
MAX_CI_RUNS = 10
PYTEST_COMMAND = "python -m pytest"


class GitError(RepositoryError):
    """A user-correctable Git or GitHub failure."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class _Resolved:
    owner: str
    repo: str
    base: str
    head_ref: str
    head_owner: str
    head_repo: str
    head_sha: str | None
    pull_request: PullRequestInfo | None


def _redact(text: str) -> str:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return text.replace(token, "[redacted]")
    return text


def _cap(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n… truncated", True


class GitService:
    """GitHub metadata, diffs, file reads, CI runs, and an isolated pytest checkout."""

    def download(self, owner: str, repo: str, destination: Path) -> str:
        """Shallow clone used by Analyze. The checkout behavior stays in github.py."""
        return download_repository(owner, repo, destination)

    def pull_request(self, url: str) -> PullRequestInfo:
        return self._load_pull_request(url)[3]

    def diff(self, owner: str, repo: str, base: str, head: str) -> DiffResult:
        owner, repo = self._identity(owner, repo)
        base_ref = self._validate_ref(base)
        head_ref = self._validate_compare_ref(head)
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/compare/"
            f"{quote(base_ref, safe='')}...{quote(head_ref, safe=':')}"
        )
        payload = self._json(url)
        commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
        head_sha = None
        if commits and isinstance(commits[-1], dict) and _SHA.fullmatch(str(commits[-1].get("sha") or "")):
            head_sha = str(commits[-1]["sha"])
        raw_files = payload.get("files") if isinstance(payload.get("files"), list) else []
        files: list[DiffFile] = []
        for item in raw_files[:MAX_DIFF_FILES]:
            if not isinstance(item, dict):
                continue
            patch = item.get("patch")
            if isinstance(patch, str):
                patch, _truncated = _cap(patch, MAX_PATCH_CHARS)
            else:
                patch = None
            files.append(
                DiffFile(
                    path=str(item.get("filename") or ""),
                    status=str(item.get("status") or "modified"),
                    additions=int(item.get("additions") or 0),
                    deletions=int(item.get("deletions") or 0),
                    patch=patch,
                )
            )
        return DiffResult(
            base=base_ref,
            head=head_ref,
            status=str(payload.get("status") or "unknown"),
            ahead_by=int(payload.get("ahead_by") or 0),
            behind_by=int(payload.get("behind_by") or 0),
            total_commits=int(payload.get("total_commits") or 0),
            head_sha=head_sha,
            truncated=len(raw_files) > MAX_DIFF_FILES,
            files=files,
        )

    def read_file(self, owner: str, repo: str, path: str, ref: str) -> FileAtRef:
        owner, repo = self._identity(owner, repo)
        relative = self._validate_path(path)
        resolved_ref = self._validate_ref(ref)
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/contents/{self._quote_path(relative)}"
            f"?{urlencode({'ref': resolved_ref})}"
        )
        payload = self._json(url)
        if payload.get("type") != "file":
            raise GitError("That path is not a file")
        size = int(payload.get("size") or 0)
        if size > MAX_FILE_BYTES:
            raise GitError("File is too large to read through the Git service")
        encoding = payload.get("encoding")
        raw = payload.get("content")
        if encoding == "base64" and isinstance(raw, str):
            try:
                text = base64.b64decode(re.sub(r"\s+", "", raw)).decode("utf-8", errors="replace")
            except ValueError as exc:
                raise GitError("GitHub returned file contents that could not be decoded") from exc
        elif isinstance(raw, str) and raw:
            text = raw
        else:
            raise GitError("GitHub did not return file text for that ref")
        text, truncated = _cap(text, MAX_FILE_CHARS)
        return FileAtRef(path=relative, ref=resolved_ref, content=text, size=size, truncated=truncated)

    def ci_runs(self, owner: str, repo: str, ref: str) -> CiReport:
        owner, repo = self._identity(owner, repo)
        resolved = self._validate_ref(ref)
        workflows_provided, workflows = self._workflow_runs(owner, repo, resolved)
        checks_provided, checks = self._check_runs(owner, repo, resolved)
        return CiReport(ref=resolved, available=workflows_provided or checks_provided, runs=workflows + checks)

    def run_pytest(self, owner: str, repo: str, ref: str) -> PytestResult:
        """Clone ref into a temp directory, run pytest, then delete the checkout."""
        owner, repo = self._identity(owner, repo)
        resolved = self._validate_ref(ref)
        with tempfile.TemporaryDirectory(prefix="xray-ci-") as temporary:
            checkout = Path(temporary) / "checkout"
            self._clone_ref(owner, repo, resolved, checkout)
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "pytest"],
                    cwd=checkout,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=PYTEST_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise GitError("pytest timed out on the pull request branch") from exc
            except FileNotFoundError as exc:
                raise GitError("pytest is not available on the server") from exc
            stdout, stdout_truncated = _cap(_redact(completed.stdout or ""), MAX_OUTPUT_CHARS)
            stderr, stderr_truncated = _cap(_redact(completed.stderr or ""), MAX_OUTPUT_CHARS)
            result = PytestResult(
                repository=f"{owner}/{repo}",
                ref=resolved,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                command=PYTEST_COMMAND,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        return result

    def check(self, target: str, branch: str | None = None, base: str | None = None) -> GitCheckResult:
        resolved = self._resolve(target, branch, base)
        warnings: list[str] = []
        compare_head = resolved.head_ref
        if resolved.head_owner.lower() != resolved.owner.lower():
            compare_head = f"{resolved.head_owner}:{resolved.head_ref}"
        diff = self.diff(resolved.owner, resolved.repo, resolved.base, compare_head)
        sha = resolved.head_sha if resolved.head_sha and _SHA.fullmatch(resolved.head_sha) else None
        if sha is None and diff.head_sha:
            sha = diff.head_sha
        file_ref = sha or resolved.head_ref
        files: list[FileAtRef] = []
        readable = [item for item in diff.files if item.status in _READABLE_STATUS and item.path]
        if len(readable) > MAX_CHECK_FILES:
            warnings.append(f"Read {MAX_CHECK_FILES} of {len(readable)} changed files")
        if diff.truncated:
            warnings.append("The diff was truncated")
        for item in readable[:MAX_CHECK_FILES]:
            try:
                files.append(self.read_file(resolved.head_owner, resolved.head_repo, item.path, file_ref))
            except GitError as exc:
                warnings.append(f"{item.path}: {exc}")
        try:
            ci = self.ci_runs(resolved.owner, resolved.repo, file_ref)
        except GitError as exc:
            warnings.append(str(exc))
            ci = CiReport(ref=file_ref, available=False, runs=[])
        pytest_result = self.run_pytest(resolved.head_owner, resolved.head_repo, resolved.head_ref)
        return GitCheckResult(
            repository=f"{resolved.owner}/{resolved.repo}",
            base=resolved.base,
            ref=resolved.head_ref,
            head_sha=file_ref,
            pull_request=resolved.pull_request,
            diff=diff,
            files=files,
            ci=ci,
            pytest=pytest_result,
            warnings=warnings,
        )

    def _resolve(self, target: str, branch: str | None, base: str | None) -> _Resolved:
        value = target.strip()
        if _PR_URL.match(value):
            owner, repo, _number, info = self._load_pull_request(value)
            head_owner, head_repo = info.head_repository.split("/", 1)
            return _Resolved(owner, repo, info.base_ref, info.head_ref, head_owner, head_repo, info.head_sha, info)
        tree = _TREE_URL.match(value)
        if tree:
            owner, repo = self._identity(tree.group(1), tree.group(2))
            ref = self._validate_ref(unquote(tree.group(3)).strip("/"))
            base_ref = self._validate_ref((base or "main").strip() or "main")
            return _Resolved(owner, repo, base_ref, ref, owner, repo, None, None)
        owner, repo = self._identity(*parse_repository_url(value))
        branch_name = (branch or "").strip()
        if not branch_name:
            raise GitError("Enter a branch when the target is a repository URL")
        ref = self._validate_ref(branch_name)
        base_ref = self._validate_ref((base or "main").strip() or "main")
        return _Resolved(owner, repo, base_ref, ref, owner, repo, None, None)

    def _load_pull_request(self, url: str) -> tuple[str, str, int, PullRequestInfo]:
        match = _PR_URL.match(url.strip())
        if not match:
            raise GitError("Enter a GitHub pull request URL such as https://github.com/owner/repository/pull/1")
        owner, repo = self._identity(match.group(1), match.group(2))
        number = int(match.group(3))
        payload = self._json(f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}")
        base = payload.get("base") if isinstance(payload.get("base"), dict) else {}
        head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
        base_ref = str(base.get("ref") or "")
        head_ref = str(head.get("ref") or "")
        head_sha = str(head.get("sha") or "")
        if not base_ref or not head_ref or not _SHA.fullmatch(head_sha):
            raise GitError("Pull request metadata is missing a base or head ref")
        self._validate_ref(base_ref)
        self._validate_ref(head_ref)
        head_repo_payload = head.get("repo") if isinstance(head.get("repo"), dict) else {}
        full_name = str(head_repo_payload.get("full_name") or f"{owner}/{repo}")
        if full_name.count("/") != 1:
            raise GitError("Pull request head repository is missing")
        head_owner, head_name = self._identity(*full_name.split("/", 1))
        body, _truncated = _cap(str(payload.get("body") or ""), 2_000)
        info = PullRequestInfo(
            number=int(payload.get("number") or number),
            title=str(payload.get("title") or ""),
            state=str(payload.get("state") or ""),
            html_url=str(payload.get("html_url") or url),
            author=str((payload.get("user") or {}).get("login") or "") if isinstance(payload.get("user"), dict) else "",
            base_ref=base_ref,
            base_sha=str(base.get("sha") or ""),
            head_ref=head_ref,
            head_sha=head_sha,
            head_repository=f"{head_owner}/{head_name}",
            body=body,
        )
        return owner, repo, number, info

    def _workflow_runs(self, owner: str, repo: str, ref: str) -> tuple[bool, list[CiRun]]:
        key = "head_sha" if _SHA.fullmatch(ref) else "branch"
        query = urlencode({key: ref, "per_page": MAX_CI_RUNS})
        try:
            payload = self._json(f"https://api.github.com/repos/{owner}/{repo}/actions/runs?{query}")
        except GitError as exc:
            if exc.status == 404:
                return False, []
            raise
        raw = payload.get("workflow_runs")
        if not isinstance(raw, list):
            return False, []
        return True, self._ci_items(raw, "workflow", ref)

    def _check_runs(self, owner: str, repo: str, ref: str) -> tuple[bool, list[CiRun]]:
        try:
            payload = self._json(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe='')}/check-runs"
                f"?{urlencode({'per_page': MAX_CI_RUNS})}"
            )
        except GitError as exc:
            if exc.status == 404:
                return False, []
            raise
        raw = payload.get("check_runs")
        if not isinstance(raw, list):
            return False, []
        return True, self._ci_items(raw, "check", ref)

    def _ci_items(self, raw: list, source: str, ref: str) -> list[CiRun]:
        runs: list[CiRun] = []
        for item in raw[:MAX_CI_RUNS]:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            runs.append(
                CiRun(
                    id=int(item["id"]),
                    name=str(item.get("name") or source),
                    status=item.get("status"),
                    conclusion=item.get("conclusion"),
                    html_url=str(item.get("html_url") or item.get("details_url") or ""),
                    head_sha=str(item.get("head_sha") or ref),
                    head_branch=item.get("head_branch"),
                    event=item.get("event"),
                    source=source,
                )
            )
        return runs

    def _clone_ref(self, owner: str, repo: str, ref: str, destination: Path) -> None:
        url = f"https://github.com/{owner}/{repo}.git"
        if _SHA.fullmatch(ref):
            self._run_git(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(destination)])
            self._run_git(["git", "-C", str(destination), "fetch", "--depth", "1", "origin", ref])
            self._run_git(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
            return
        self._run_git(["git", "clone", "--depth", "1", "--branch", ref, "--single-branch", url, str(destination)])

    def _run_git(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=CLONE_TIMEOUT_SECONDS,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError("Cloning the ref timed out") from exc
        except FileNotFoundError as exc:
            raise GitError("Git is not available on the server") from exc
        if completed.returncode != 0:
            detail = _redact((completed.stderr or completed.stdout or "git failed").strip())
            tail = detail.splitlines()[-1][:300] if detail else "git failed"
            raise GitError(f"Could not check out that ref. {tail}")
        return completed

    def _json(self, url: str) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "AI-Codebase-X-Ray/ci"}
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GitError("GitHub could not find that repository, pull request, ref, or file", status=404) from exc
            if exc.code == 403:
                raise GitError("GitHub rate limit reached. Add GITHUB_TOKEN to .env and try again", status=403) from exc
            if exc.code == 401:
                raise GitError("GitHub rejected GITHUB_TOKEN. Check the token in .env", status=401) from exc
            raise GitError(f"GitHub returned HTTP {exc.code}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitError("Could not reach GitHub. Check your network connection") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GitError("GitHub returned an unexpected response") from exc
        if not isinstance(payload, dict):
            raise GitError("GitHub returned an unexpected response")
        return payload

    def _identity(self, owner: str, repo: str) -> tuple[str, str]:
        owner_name = owner.strip()
        repo_name = repo.strip().removesuffix(".git")
        if not _NAME.fullmatch(owner_name) or not _NAME.fullmatch(repo_name):
            raise GitError("Repository owner or name is not valid")
        return owner_name, repo_name

    def _validate_ref(self, ref: str) -> str:
        value = ref.strip()
        if (
            not value
            or value.startswith(("-", "/"))
            or value.endswith("/")
            or ".." in value
            or "//" in value
            or not _REF.fullmatch(value)
            or len(value) > 200
        ):
            raise GitError("Branch or ref contains unsupported characters")
        return value

    def _validate_compare_ref(self, ref: str) -> str:
        if ref.count(":") == 1:
            owner, branch = ref.split(":", 1)
            return f"{self._identity(owner, 'repo')[0]}:{self._validate_ref(branch)}"
        return self._validate_ref(ref)

    def _validate_path(self, path: str) -> str:
        cleaned = path.strip()
        if not cleaned or "\x00" in cleaned or "\\" in cleaned or cleaned.startswith(("/", "~")):
            raise GitError("File path must stay inside the repository")
        parts = Path(cleaned).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise GitError("File path must stay inside the repository")
        return Path(cleaned).as_posix()

    def _quote_path(self, path: str) -> str:
        return "/".join(quote(part, safe="") for part in path.split("/"))
