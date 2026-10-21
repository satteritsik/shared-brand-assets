"""Return the code-check findings for an edited file to the agent that edited it (Claude Code PostToolUse).

The hook finds the edited file's repository and hands the file to that repository's own runner,
`.code-checks/run.py edit <file>`, which `polyrepo checks-sync` writes into every repository with the
checks of its stacks. The runner checks the file in the repository's own environment (its `.venv`
and `node_modules`), so third-party imports resolve as they do for the repository itself. A file
outside a repository, or in one without the runner, is not checked.

Exit status: 0 when the file is clean or not checked; 2 with the findings on stderr otherwise, which
Claude Code returns to the agent. The hook fixes nothing.

Every checked edit, clean ones included, is appended as one JSON line to
`$SKILLSPOKE_LOGS/code-checks/edits.jsonl`, so the owner's code-checks report can compute how often
agents write clean code the first time and which rules they break most. The record carries the
time, session, agent id and type, editing tool, repository, file, and per check the tool, whether it
was clean and the rule id of each finding (from the runner's summary, `CODE_CHECKS_SUMMARY`). A
logging failure is reported on stderr and never changes the hook's result; when `SKILLSPOKE_LOGS`
is unset nothing is logged and the hook says so once per session.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs git and the runner with list argv and no shell
import sys
import tempfile
from pathlib import Path
from typing import Final

RUNNER: Final = Path(".code-checks") / "run.py"
RUNNER_TIMEOUT_SECONDS: Final = 290
EXIT_CLEAN: Final = 0
EXIT_REPORT: Final = 2
RUNNER_FINDINGS: Final = 1
GIT_PATHS_ASKED: Final = 2
LOGS_ENV: Final = "SKILLSPOKE_LOGS"
SUMMARY_ENV: Final = "CODE_CHECKS_SUMMARY"
LOG_FILE: Final = Path("code-checks") / "edits.jsonl"
UNSET_MARKER_PREFIX: Final = "code-checks-logs-unset-"


class HookInputError(ValueError):
    """The hook's stdin is not the PostToolUse event this hook expects."""


@dataclasses.dataclass(frozen=True)
class EditEvent:
    """What the hook takes from the PostToolUse event."""

    path: Path | None
    session_id: str | None
    agent_id: str | None
    agent_type: str | None
    tool_name: str | None


@dataclasses.dataclass(frozen=True)
class Tree:
    """The working tree holding the edited file, and the repository it belongs to."""

    root: Path
    repository: str


def _text_field(event: dict[str, object], key: str) -> str | None:
    """Read an optional string field of the event.

    Args:
        event: The parsed event.
        key: The field.

    Returns:
        The string, or None when it is absent or not a non-empty string.

    """
    value = event.get(key)
    return value if isinstance(value, str) and value else None


def parse_event(raw: str) -> EditEvent:
    """Read the edited file and the editing agent's identity from the PostToolUse event.

    Args:
        raw: The event JSON from stdin.

    Returns:
        The event's fields; `agent_id` and `agent_type` are None on the main thread.

    Raises:
        HookInputError: When the event is not a JSON object or a relative path comes without `cwd`.

    """
    try:
        event: object = json.loads(raw)
    except json.JSONDecodeError as error:
        message = f"hook input is not JSON: {error}"
        raise HookInputError(message) from error
    if not isinstance(event, dict):
        message = "hook input is not a JSON object"
        raise HookInputError(message)
    fields: dict[str, object] = {str(key): value for key, value in event.items()}
    return EditEvent(
        edited_file(fields),
        _text_field(fields, "session_id"),
        _text_field(fields, "agent_id"),
        _text_field(fields, "agent_type"),
        _text_field(fields, "tool_name"),
    )


def edited_file(event: dict[str, object]) -> Path | None:
    """Read the edited file's path from the PostToolUse event.

    Args:
        event: The parsed event.

    Returns:
        The absolute path, or None when the tool input names no file.

    Raises:
        HookInputError: When a relative path comes without `cwd`.

    """
    tool_input: object = event.get("tool_input")
    value: object = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    cwd: object = event.get("cwd")
    if not isinstance(cwd, str):
        message = "hook input: a relative file_path needs cwd"
        raise HookInputError(message)
    return Path(cwd) / path


def repository_of(path: Path) -> Tree | None:
    """Find the working tree holding a file and the repository it belongs to.

    Args:
        path: The file.

    Returns:
        The tree, named after the primary working tree's folder (the same for every worktree of a
        repository), or None when the file is in no git working tree.

    """
    if not path.parent.is_dir():
        return None
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        ["git", "-C", str(path.parent), "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],  # ruff: ignore[start-process-with-partial-path] - git from PATH like every developer tool
        capture_output=True,
        text=True,
        check=False,
    )
    lines = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or len(lines) != GIT_PATHS_ASKED:
        return None
    root, common = Path(lines[0]), Path(lines[1])
    return Tree(root, common.parent.name if common.name == ".git" else root.name)


def run_checks(tree: Path, path: Path, summary: Path) -> tuple[int, str]:
    """Run the repository's runner on the edited file.

    Args:
        tree: The working tree.
        path: The edited file.
        summary: The file the runner writes its per-check summary to (a runner older than the
            summary ignores it).

    Returns:
        The runner's exit status and its output.

    """
    try:
        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            [sys.executable, str(tree / RUNNER), "edit", str(path)],
            cwd=tree,
            env={**os.environ, SUMMARY_ENV: str(summary)},
            capture_output=True,
            text=True,
            timeout=RUNNER_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return EXIT_REPORT, f"running {tree / RUNNER} failed: {error}"
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def read_summary(summary: Path) -> list[dict[str, object]] | None:
    """Read the runner's per-check summary.

    Args:
        summary: The summary file.

    Returns:
        One entry per check that ran, or None when the runner wrote no summary (an older runner,
        or one that could not run).

    Raises:
        ValueError: When the file holds something other than a JSON list of objects.

    """
    text = summary.read_text(encoding="utf-8") if summary.is_file() else ""
    if not text:
        return None
    parsed: object = json.loads(text)
    if not isinstance(parsed, list) or not all(isinstance(entry, dict) for entry in parsed):
        message = f"{summary}: the runner's summary is not a list of objects"
        raise ValueError(message)
    return [{str(key): value for key, value in entry.items()} for entry in parsed]


def finding_count(checks: list[dict[str, object]] | None, status: int) -> int:
    """Count the findings of an edit.

    Args:
        checks: The runner's summary, or None without one.
        status: The runner's exit status.

    Returns:
        The number of rule ids across the checks; without a summary, 0 when the runner exited
        clean and 1 otherwise.

    """
    if checks is None:
        return 0 if status == EXIT_CLEAN else 1
    return sum(len(rules) for entry in checks if isinstance(rules := entry.get("rules"), list))


def log_directory(session_id: str | None) -> Path | None:
    """Return the log folder under `SKILLSPOKE_LOGS`, saying once per session when it is unset.

    Args:
        session_id: The session, to say only once that nothing is logged.

    Returns:
        The folder, or None when `SKILLSPOKE_LOGS` is unset.

    """
    logs = os.environ.get(LOGS_ENV)
    if logs:
        return Path(logs)
    marker = Path(tempfile.gettempdir()) / f"{UNSET_MARKER_PREFIX}{session_id or 'unknown'}"
    if not marker.exists():
        marker.touch()
        sys.stderr.write(f"code-checks hook: {LOGS_ENV} is not set; edits are not logged\n")
    return None


def log_edit(event: EditEvent, tree: Tree, path: Path, status: int, summary: Path) -> None:
    """Append one record of the edit to the edit log.

    Args:
        event: The PostToolUse event.
        tree: The working tree.
        path: The edited file.
        status: The runner's exit status.
        summary: The runner's summary file.

    An unreadable or malformed summary and an unwritable log propagate as `OSError` and
    `ValueError` from the helpers; the caller reports them.

    """
    logs = log_directory(event.session_id)
    checks = read_summary(summary)
    if logs is None or checks == []:
        return
    count = finding_count(checks, status)
    record = {
        "time": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "agent_type": event.agent_type,
        "edit_tool": event.tool_name,
        "repository": tree.repository,
        "tree": str(tree.root),
        "file": path.relative_to(tree.root).as_posix() if path.is_relative_to(tree.root) else str(path),
        "runner_status": status,
        "checks": checks,
        "findings": count,
        "clean": count == 0,
    }
    log_file = logs / LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(log_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def check_and_log(event: EditEvent, tree: Tree, path: Path) -> tuple[int, str]:
    """Run the checks on the edited file and log the edit.

    Args:
        event: The PostToolUse event.
        tree: The working tree.
        path: The edited file.

    Returns:
        The runner's exit status and its output.

    """
    with tempfile.TemporaryDirectory(prefix="code-checks-") as scratch:
        summary = Path(scratch) / "summary.json"
        status, output = run_checks(tree.root, path, summary)
        try:
            log_edit(event, tree, path, status, summary)
        except (OSError, ValueError) as error:
            sys.stderr.write(f"code-checks hook: logging the edit failed: {error}\n")
    return status, output


def main() -> int:
    """Check the edited file and report its findings.

    Returns:
        0 when clean or not checked, 2 when there are findings or the check could not run.

    """
    try:
        event = parse_event(sys.stdin.read())
    except HookInputError as error:
        sys.stderr.write(f"code-checks hook: {error}\n")
        return EXIT_REPORT
    path = event.path
    tree = repository_of(path) if path is not None else None
    if path is None or tree is None or not (tree.root / RUNNER).is_file():
        return EXIT_CLEAN
    status, output = check_and_log(event, tree, path)
    if status == EXIT_CLEAN:
        return EXIT_CLEAN
    heading = "findings" if status == RUNNER_FINDINGS else "the checks could not run"
    sys.stderr.write(f"Code checks on {path} ({heading}):\n{output}\n")
    return EXIT_REPORT


if __name__ == "__main__":
    sys.exit(main())
