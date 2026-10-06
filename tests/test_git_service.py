import base64
import io
import json
import subprocess
import sys
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from backend.github import RepositoryError
from backend.main import app
from backend.models import CiReport, DiffResult, GitCheckResult, PytestResult
from backend.services.git import GitError, GitService
from backend.store import NetworkXGraphStore

HEAD_SHA = "b" * 40
BASE_SHA = "a" * 40


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status = 200
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> bool:
        return False


def headers_of(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def http_error(url: str, code: int) -> HTTPError:
    return HTTPError(url, code, "error", Message(), io.BytesIO(b"{}"))


@pytest.fixture(autouse=True)
def block_git_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def blocked_urlopen(*_args, **_kwargs):
        raise AssertionError("network is disabled in Git service tests")

    def blocked_run(*_args, **_kwargs):
        raise AssertionError("git/pytest process is disabled in Git service tests")

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", blocked_urlopen)
    monkeypatch.setattr("backend.services.git.subprocess.run", blocked_run)


def pull_payload() -> dict:
    return {
        "number": 7,
        "title": "Add billing",
        "state": "open",
        "html_url": "https://github.com/acme/widgets/pull/7",
        "user": {"login": "ada"},
        "body": "Fixes totals",
        "base": {"ref": "main", "sha": BASE_SHA},
        "head": {"ref": "feature", "sha": HEAD_SHA, "repo": {"full_name": "ada/widgets"}},
    }


def compare_payload() -> dict:
    return {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "commits": [{"sha": HEAD_SHA}],
        "files": [
            {
                "filename": "app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "patch": "@@ -1 +1,2 @@\n+x\n",
            }
        ],
    }


def file_payload() -> dict:
    encoded = base64.b64encode(b"def add():\n    return 1\n").decode("ascii")
    return {
        "type": "file",
        "encoding": "base64",
        "content": encoded[:4] + "\n" + encoded[4:],
        "size": 24,
        "path": "app.py",
    }


def workflow_payload() -> dict:
    return {
        "total_count": 1,
        "workflow_runs": [
            {
                "id": 99,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/acme/widgets/actions/runs/99",
                "head_sha": HEAD_SHA,
                "head_branch": "feature",
                "event": "pull_request",
            }
        ],
    }


def check_payload() -> dict:
    return {
        "check_runs": [
            {
                "id": 5,
                "name": "pytest",
                "status": "completed",
                "conclusion": "success",
                "details_url": "https://github.com/acme/widgets/runs/5",
                "head_sha": HEAD_SHA,
            }
        ]
    }


def route_github(urls: dict[str, dict]):
    def urlopen(request, timeout=20):
        matched = next((payload for fragment, payload in urls.items() if fragment in request.full_url), None)
        if matched is None:
            raise AssertionError(request.full_url)
        return FakeResponse(matched)

    return urlopen


def clone_then_pytest(exit_code: int, stdout: str, stderr: str, commands: list[list[str]] | None = None):
    def fake_run(command, **kwargs):
        if commands is not None:
            commands.append(command)
        if command[0] == "git":
            if command[1] == "clone":
                Path(command[-1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        assert kwargs.get("check") is False
        assert kwargs.get("cwd")
        assert Path(kwargs["cwd"]).is_dir()
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)

    return fake_run


def test_pull_request_metadata_does_not_send_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=20):
        assert request.full_url.endswith("/repos/acme/widgets/pulls/7")
        headers = headers_of(request)
        assert headers["accept"] == "application/vnd.github+json"
        assert "authorization" not in headers
        return FakeResponse(pull_payload())

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    info = GitService().pull_request("https://github.com/acme/widgets/pull/7")
    assert info.number == 7
    assert info.title == "Add billing"
    assert info.author == "ada"
    assert info.base_ref == "main"
    assert info.head_ref == "feature"
    assert info.head_sha == HEAD_SHA
    assert info.head_repository == "ada/widgets"


def test_github_request_uses_bearer_token_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    def urlopen(request, timeout=20):
        assert headers_of(request)["authorization"] == "Bearer test-token"
        return FakeResponse(pull_payload())

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    info = GitService().pull_request("https://github.com/acme/widgets/pull/7/")
    assert info.state == "open"


def test_diff_between_branches_quotes_the_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=20):
        assert "/repos/acme/widgets/compare/main...feature%2Ffoo" in request.full_url
        return FakeResponse(compare_payload())

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    diff = GitService().diff("acme", "widgets", "main", "feature/foo")
    assert diff.status == "ahead"
    assert diff.ahead_by == 1
    assert diff.head_sha == HEAD_SHA
    assert diff.files[0].path == "app.py"
    assert diff.files[0].additions == 2
    assert "+x" in (diff.files[0].patch or "")


def test_read_file_decodes_contents_at_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=20):
        assert "/repos/acme/widgets/contents/pkg/app.py" in request.full_url
        assert f"ref={HEAD_SHA}" in request.full_url
        return FakeResponse(file_payload())

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    viewed = GitService().read_file("acme", "widgets", "pkg/app.py", HEAD_SHA)
    assert viewed.content == "def add():\n    return 1\n"
    assert viewed.ref == HEAD_SHA
    assert viewed.truncated is False


def test_read_file_rejects_parent_segments() -> None:
    with pytest.raises(GitError):
        GitService().read_file("acme", "widgets", "../secrets.env", "main")


def test_ci_runs_when_the_github_api_provides_them(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=20):
        if "/actions/runs" in request.full_url:
            assert f"head_sha={HEAD_SHA}" in request.full_url
            return FakeResponse(workflow_payload())
        if "/check-runs" in request.full_url:
            return FakeResponse(check_payload())
        raise AssertionError(request.full_url)

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    report = GitService().ci_runs("acme", "widgets", HEAD_SHA)
    assert report.available is True
    assert [run.name for run in report.runs] == ["CI", "pytest"]
    assert report.runs[0].source == "workflow"
    assert report.runs[1].source == "check"
    assert report.runs[1].html_url.endswith("/runs/5")


def test_ci_runs_are_absent_when_github_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=20):
        raise http_error(request.full_url, 404)

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    report = GitService().ci_runs("acme", "widgets", "feature")
    assert report.available is False
    assert report.runs == []


def test_ci_runs_are_absent_when_the_payload_omits_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.git.urllib.request.urlopen",
        route_github({"/actions/runs": {"total_count": 0}, "/check-runs": {}}),
    )
    report = GitService().ci_runs("acme", "widgets", "feature")
    assert report.available is False
    assert report.runs == []


def test_pytest_captures_exit_code_stdout_stderr_and_deletes_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        if command[0] == "git":
            assert "--branch" in command
            assert "feature" in command
            assert "https://github.com/acme/widgets.git" in command
            assert "token" not in " ".join(command)
            Path(command[-1]).mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        seen["cwd"] = kwargs["cwd"]
        seen["during"] = Path(kwargs["cwd"]).is_dir()
        assert command[0] == sys.executable
        assert command[1:] == ["-m", "pytest"]
        assert kwargs.get("check") is False
        return subprocess.CompletedProcess(command, 2, "stdout-line\n", "stderr-line\n")

    monkeypatch.setattr("backend.services.git.subprocess.run", fake_run)
    result = GitService().run_pytest("acme", "widgets", "feature")
    assert result.exit_code == 2
    assert result.stdout == "stdout-line\n"
    assert result.stderr == "stderr-line\n"
    assert result.command == "python -m pytest"
    assert seen["during"] is True
    assert not Path(seen["cwd"]).exists()


def test_pytest_redacts_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setattr(
        "backend.services.git.subprocess.run",
        clone_then_pytest(1, "leaked secret-token-value\n", "secret-token-value"),
    )
    result = GitService().run_pytest("acme", "widgets", "feature")
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr
    assert "[redacted]" in result.stdout


def test_pytest_truncates_large_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.services.git.MAX_OUTPUT_CHARS", 5)
    monkeypatch.setattr("backend.services.git.subprocess.run", clone_then_pytest(0, "abcdefghij", ""))
    result = GitService().run_pytest("acme", "widgets", "feature")
    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("abcde")


def test_clone_failure_does_not_run_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **_kwargs):
        assert command[0] == "git"
        return subprocess.CompletedProcess(command, 128, "", "fatal: repository not found")

    monkeypatch.setattr("backend.services.git.subprocess.run", fake_run)
    with pytest.raises(GitError, match="repository not found"):
        GitService().run_pytest("acme", "widgets", "feature")


def test_pytest_timeout_still_deletes_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        if command[0] == "git":
            destination = Path(command[-1])
            destination.mkdir(parents=True)
            created["path"] = destination
            return subprocess.CompletedProcess(command, 0, "", "")
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 1)

    monkeypatch.setattr("backend.services.git.subprocess.run", fake_run)
    with pytest.raises(GitError, match="timed out"):
        GitService().run_pytest("acme", "widgets", "feature")
    assert not created["path"].exists()


def test_sha_checkout_fetches_the_commit_instead_of_a_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("backend.services.git.subprocess.run", clone_then_pytest(0, "ok\n", "", commands))
    result = GitService().run_pytest("acme", "widgets", HEAD_SHA)
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    clone = commands[0]
    assert "--branch" not in clone
    assert "--no-checkout" in clone
    assert commands[1][1:5] == ["-C", commands[0][-1], "fetch", "--depth"]
    assert commands[1][-1] == HEAD_SHA
    assert commands[2][-2:] == ["--detach", "FETCH_HEAD"]
    assert commands[3][1:] == ["-m", "pytest"]
    assert not Path(commands[0][-1]).exists()


def test_option_like_ref_is_rejected_before_git_runs() -> None:
    with pytest.raises(GitError, match="unsupported"):
        GitService().run_pytest("acme", "widgets", "--upload-pack=evil")


def test_check_pull_request_orchestrates_metadata_diff_ci_and_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def urlopen(request, timeout=20):
        url = request.full_url
        assert "authorization" not in headers_of(request)
        if url.endswith("/pulls/7"):
            return FakeResponse(pull_payload())
        if "/compare/" in url:
            assert "main...ada:feature" in url
            return FakeResponse(compare_payload())
        if "/actions/runs" in url:
            assert f"head_sha={HEAD_SHA}" in url
            return FakeResponse(workflow_payload())
        if "/check-runs" in url:
            return FakeResponse(check_payload())
        if "/contents/app.py" in url and "ada/widgets" in url:
            return FakeResponse(file_payload())
        raise AssertionError(url)

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("backend.services.git.subprocess.run", clone_then_pytest(3, "failed stdout\n", "failed stderr\n", commands))
    result = GitService().check("https://github.com/acme/widgets/pull/7")
    assert result.repository == "acme/widgets"
    assert result.pull_request is not None
    assert result.pull_request.head_repository == "ada/widgets"
    assert result.base == "main"
    assert result.ref == "feature"
    assert result.diff.files[0].path == "app.py"
    assert result.files[0].content.startswith("def add()")
    assert result.ci.available is True
    assert result.ci.runs[0].name == "CI"
    assert result.pytest.exit_code == 3
    assert result.pytest.stdout == "failed stdout\n"
    assert result.pytest.stderr == "failed stderr\n"
    assert result.pytest.repository == "ada/widgets"
    assert "https://github.com/ada/widgets.git" in commands[0]
    assert not Path(commands[0][-1]).exists()


def test_check_branch_diffs_against_main_and_runs_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def urlopen(request, timeout=20):
        url = request.full_url
        assert "/pulls/" not in url
        if "/compare/develop...feature" in url:
            return FakeResponse(compare_payload())
        if "/actions/runs" in url or "/check-runs" in url:
            return FakeResponse({"workflow_runs": [], "check_runs": []})
        if "/contents/app.py" in url:
            return FakeResponse(file_payload())
        raise AssertionError(url)

    monkeypatch.setattr("backend.services.git.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("backend.services.git.subprocess.run", clone_then_pytest(0, "1 passed\n", "", commands))
    result = GitService().check("https://github.com/acme/widgets", branch="feature", base="develop")
    assert result.pull_request is None
    assert result.base == "develop"
    assert result.ref == "feature"
    assert result.pytest.exit_code == 0
    assert result.pytest.stdout == "1 passed\n"
    assert result.ci.available is True
    assert result.ci.runs == []
    assert "--branch" in commands[0]
    assert "https://github.com/acme/widgets.git" in commands[0]


def test_repository_url_requires_a_branch() -> None:
    with pytest.raises(GitError, match="branch"):
        GitService().check("https://github.com/acme/widgets")


def test_invalid_target_never_calls_github() -> None:
    with pytest.raises(RepositoryError):
        GitService().check("not a repository")


def test_other_backend_modules_do_not_call_git_or_github() -> None:
    root = Path(__file__).resolve().parents[1] / "backend"
    allowed = {"github.py", "git.py"}
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess" in text or "api.github.com" in text or "github.com/" in text:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def test_git_check_endpoint_returns_the_captured_pytest_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_check(self, target: str, branch: str | None = None, base: str | None = None) -> GitCheckResult:
        assert target == "https://github.com/acme/widgets/pull/7"
        assert branch is None
        return GitCheckResult(
            repository="acme/widgets",
            base="main",
            ref="feature",
            head_sha=HEAD_SHA,
            diff=DiffResult(base="main", head="feature", status="ahead"),
            ci=CiReport(ref=HEAD_SHA, available=False),
            pytest=PytestResult(
                repository="acme/widgets",
                ref="feature",
                exit_code=1,
                stdout="boom\n",
                stderr="trace\n",
                command="python -m pytest",
            ),
        )

    monkeypatch.setattr(GitService, "check", fake_check)
    response = TestClient(app).post("/api/git/check", json={"target": "https://github.com/acme/widgets/pull/7"})
    assert response.status_code == 200
    body = response.json()
    assert body["pytest"]["exit_code"] == 1
    assert body["pytest"]["stdout"] == "boom\n"
    assert body["pytest"]["stderr"] == "trace\n"
    assert body["ci"]["available"] is False


def test_git_check_endpoint_maps_git_errors_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_check(self, target: str, branch: str | None = None, base: str | None = None) -> GitCheckResult:
        raise GitError("Could not check out that ref")

    monkeypatch.setattr(GitService, "check", fake_check)
    response = TestClient(app).post("/api/git/check", json={"target": "https://github.com/acme/widgets/pull/7"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Could not check out that ref"


def test_analyze_still_downloads_through_the_git_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_download(self, owner: str, repo: str, destination: Path) -> str:
        source = destination / "source" / repo
        source.mkdir(parents=True)
        (source / "app.py").write_text("def ready():\n    return 1\n", encoding="utf-8")
        assert owner == "acme"
        return "main"

    monkeypatch.setattr(GitService, "download", fake_download)
    monkeypatch.setattr("backend.main.store", NetworkXGraphStore(tmp_path))
    response = TestClient(app).post("/api/analyze", json={"repository_url": "https://github.com/acme/widgets"})
    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == "acme/widgets"
    assert body["default_branch"] == "main"
    assert any(symbol["name"] == "ready" for symbol in body["symbols"])
