"""Run a repository's canonical code checks after an edit, at commit, or over the whole tree.

The steward writes this file, its sibling `checks.json` and the canonical configurations into the
repository; the edit hook, the commit hook and the repository's `lint` task all run it, so every
caller checks with one definition. Usage:

    python3 .code-checks/run.py edit <file>
    python3 .code-checks/run.py commit <hook-id> [file ...]
    python3 .code-checks/run.py all [stack ...]

Each check runs in the repository's own environment: Python tools against the repository's
`.venv` (the primary working tree's when a worktree has none), Node tools from its
`node_modules`. Exit status: 0 clean, 1 findings, 2 usage error. Findings go to stdout.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import configparser
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - every command is a fixed argv list run without a shell
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

CHECKS_DIR: Final = Path(__file__).resolve().parent
TREE: Final = CHECKS_DIR.parent
SPEC_FILE: Final = CHECKS_DIR / "checks.json"
CDK_NAG_RUNNER: Final = CHECKS_DIR / "cdk_nag_synth.py"
CDK_NAG_CONFIG: Final = CHECKS_DIR / "cdk-nag.json"
MYPY_CONFIG: Final = TREE / "mypy.ini"
ESLINT_BASE: Final = "eslint.base.mjs"
ESLINT_OWN_CONFIGS: Final = tuple(f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts"))
PYTHON_SUFFIXES: Final = frozenset({".py", ".pyi"})
TYPESCRIPT_SUFFIXES: Final = frozenset({".ts", ".tsx", ".mts", ".cts"})
JAVASCRIPT_SUFFIXES: Final = frozenset({".js", ".jsx", ".mjs", ".cjs"})
SHELL_SUFFIXES: Final = frozenset({".sh", ".bash"})
SHELL_SHEBANG: Final = re.compile(r"^#!.*\b(ba)?sh\b")
CDK_FILES: Final = frozenset({"cdk.json", "cdk.context.json"})
CONFIG_SUFFIXES: Final = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"})
TOOL_TIMEOUT_SECONDS: Final = 600
EXIT_CLEAN: Final = 0
EXIT_FINDINGS: Final = 1
EXIT_USAGE: Final = 2
FINDINGS_EXIT_CODES: Final = frozenset({1})
NODE_TOOLCHAIN: Final = ("eslint", "@eslint/js", "globals", "typescript-eslint", "typescript", "prettier")


class CheckEnvironmentError(Exception):
    """The repository lacks the environment a check needs (no `.venv`, no `node_modules`, no tool)."""


@dataclasses.dataclass(frozen=True)
class Spec:
    """What `checks.json` says about this repository."""

    stacks: frozenset[str]
    pins: dict[str, str]
    node_pins: dict[str, str]
    cdk_apps: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Outcome:
    """One check's result: its name, whether it found nothing, and what it printed."""

    check: str
    clean: bool
    output: str


# ------------------------------------------------------------------------------------------
# the spec


def _string_map(value: object, what: str) -> dict[str, str]:
    """Validate a JSON object of strings.

    Args:
        value: The parsed value.
        what: The value's name, for the error.

    Returns:
        The mapping.

    Raises:
        ValueError: When the value is not an object of strings.

    """
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        message = f"{SPEC_FILE}: {what} must be an object of strings"
        raise ValueError(message)
    return {str(k): str(v) for k, v in value.items()}


def _string_list(value: object, what: str) -> tuple[str, ...]:
    """Validate a JSON list of strings.

    Args:
        value: The parsed value.
        what: The value's name, for the error.

    Returns:
        The strings.

    Raises:
        ValueError: When the value is not a list of strings.

    """
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"{SPEC_FILE}: {what} must be a list of strings"
        raise ValueError(message)
    return tuple(str(item) for item in value)


def load_spec() -> Spec:
    """Read `checks.json` beside this file.

    Returns:
        The spec.

    Raises:
        ValueError: When the file is missing or malformed.
        TypeError: When the file does not hold an object.

    """
    try:
        parsed: object = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = f"reading {SPEC_FILE} failed: {error}"
        raise ValueError(message) from error
    if not isinstance(parsed, dict):
        message = f"{SPEC_FILE}: expected an object"
        raise TypeError(message)
    pins: object = parsed.get("pins")
    node: object = pins.get("node", {}) if isinstance(pins, dict) else None
    flat_pins = {key: value for key, value in (pins or {}).items() if key != "node"} if isinstance(pins, dict) else pins
    return Spec(
        frozenset(_string_list(parsed.get("stacks"), "stacks")),
        _string_map(flat_pins, "pins"),
        _string_map(node, "pins.node"),
        _string_list(parsed.get("cdk_apps", []), "cdk_apps"),
    )


# ------------------------------------------------------------------------------------------
# running tools


def run_tool(check: str, argv: Sequence[str], cwd: Path = TREE) -> Outcome:
    """Run one tool and turn its exit status into an outcome.

    Args:
        check: The check's name.
        argv: The command.
        cwd: Where to run it.

    Returns:
        Clean when the tool exits 0; its stdout and stderr otherwise.

    """
    try:
        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list, no shell
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Outcome(check, clean=False, output=f"running {argv[0]} failed: {error}")
    if completed.returncode == 0:
        return Outcome(check, clean=True, output="")
    return Outcome(check, clean=False, output=(completed.stdout + completed.stderr).strip())


def _guarded(check: str, produce: Callable[[], list[Outcome]]) -> list[Outcome]:
    """Run a check, turning a missing environment into a failed outcome that says what to do.

    Args:
        check: The check's name.
        produce: Runs the check.

    Returns:
        The check's outcomes.

    """
    try:
        return produce()
    except CheckEnvironmentError as error:
        return [Outcome(check, clean=False, output=f"no environment: {error}")]


def _git(*args: str) -> str | None:
    """Run git in the tree.

    Args:
        args: The git arguments.

    Returns:
        The stripped output, or None when git fails.

    """
    outcome = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list, no shell
        ["git", "-C", str(TREE), *args],  # ruff: ignore[start-process-with-partial-path] - git is found on PATH like every developer tool
        capture_output=True,
        text=True,
        check=False,
    )
    return outcome.stdout.strip() if outcome.returncode == 0 else None


def primary_tree() -> Path:
    """Return the primary working tree of this repository (the tree itself unless it is a worktree).

    Returns:
        The folder holding the repository's `.git` folder.

    """
    common: str | None = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None or Path(common).name != ".git":
        return TREE
    return Path(common).parent


def tracked(suffixes: Iterable[str]) -> list[str]:
    """List the files git tracks or does not ignore that carry one of the suffixes.

    Args:
        suffixes: The suffixes, each with its dot.

    Returns:
        The paths relative to the tree, outside `.code-checks/`.

    """
    listing: str = _git("ls-files", "--cached", "--others", "--exclude-standard") or ""
    wanted = frozenset(suffixes)
    return [rel for rel in listing.splitlines() if Path(rel).suffix in wanted and not rel.startswith(".code-checks/")]


def _uvx() -> str:
    """Find uvx.

    Returns:
        Its path.

    Raises:
        CheckEnvironmentError: When uv is not installed.

    """
    found = shutil.which("uvx")
    if found is None:
        message = "uvx is not on PATH: install uv"
        raise CheckEnvironmentError(message)
    return found


# ------------------------------------------------------------------------------------------
# Python


def python_environment() -> Path | None:
    """Find the repository's own Python interpreter.

    Returns:
        The `.venv` interpreter of this tree or of the primary tree, or None when the repository
        declares no Python project (its Python files are checked without third-party packages).

    Raises:
        CheckEnvironmentError: When the repository has a Python project but no environment.

    """
    candidates = (TREE / ".venv" / "bin" / "python", primary_tree() / ".venv" / "bin" / "python")
    found = next((candidate for candidate in candidates if candidate.exists()), None)
    if found is not None:
        return found
    if (TREE / "pyproject.toml").is_file():
        message = "the repository has a pyproject.toml but no .venv: run `task install`"
        raise CheckEnvironmentError(message)
    return None


def _mypy_excluded() -> re.Pattern[str] | None:
    """Read the `exclude` pattern of the repository's mypy.ini.

    Returns:
        The compiled pattern, or None when the config sets none.

    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(MYPY_CONFIG, encoding="utf-8")
    pattern = parser.get("mypy", "exclude", fallback="").strip()
    return re.compile(pattern) if pattern else None


def _mypy_targets(files: Sequence[str]) -> list[str]:
    """Drop the files the mypy config excludes (mypy checks every file it is handed).

    Args:
        files: Candidate paths relative to the tree.

    Returns:
        The paths mypy should check.

    """
    excluded = _mypy_excluded()
    return [rel for rel in files if excluded is None or not excluded.search(rel)]


def ruff_checks(mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run `ruff check` and `ruff format`; at commit they fix, elsewhere they only report.

    Args:
        mode: `edit`, `commit` or `all`.
        files: The paths to check (all: ignored, the whole tree is checked).
        spec: The spec.

    Returns:
        The two outcomes.

    """
    ruff = [_uvx(), f"ruff@{spec.pins['ruff']}"]
    targets = ["."] if mode == "all" else ["--force-exclude", *files]
    if mode == "commit":
        fixed = run_tool("ruff check --fix", [*ruff, "check", "--fix", *targets])
        return [fixed, run_tool("ruff format", [*ruff, "format", *targets])]
    return [
        run_tool("ruff check", [*ruff, "check", "--no-fix", *targets]),
        run_tool("ruff format --check", [*ruff, "format", "--check", *targets]),
    ]


def mypy_check(mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run mypy at the pinned version against the repository's own interpreter and packages.

    Args:
        mode: `edit`, `commit` or `all`.
        files: The paths to check (all: every tracked Python file).
        spec: The spec.

    Returns:
        One outcome.

    """
    targets = _mypy_targets(tracked(PYTHON_SUFFIXES) if mode == "all" else files)
    if not targets:
        return []
    interpreter = python_environment()
    mypy = f"mypy@{spec.pins['mypy']}"
    if interpreter is None:
        return [run_tool("mypy", [_uvx(), mypy, *targets])]
    argv = [_uvx(), "--python", str(interpreter), mypy, "--python-executable", str(interpreter), *targets]
    return [run_tool("mypy", argv)]


# ------------------------------------------------------------------------------------------
# CDK


def cdk_app_folder(cdk_json: str) -> Path | None:
    """Return the folder of the app script a `cdk.json` runs.

    Args:
        cdk_json: The `cdk.json` path relative to the tree.

    Returns:
        The folder, or None when the file names no app command.

    """
    try:
        parsed: object = json.loads((TREE / cdk_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    command: object = parsed.get("app") if isinstance(parsed, dict) else None
    if not isinstance(command, str) or not command.split():
        return None
    return ((TREE / cdk_json).parent / command.split()[-1]).resolve().parent


def apps_for(path: Path, spec: Spec) -> list[str]:
    """Return the CDK apps an edited file belongs to.

    Args:
        path: The edited file.
        spec: The spec.

    Returns:
        The `cdk.json` paths whose app folder holds the file, or whose folder holds the edited
        `cdk.json` or `cdk.context.json`.

    """
    if path.name in CDK_FILES:
        return [app for app in spec.cdk_apps if (TREE / app).parent.resolve() == path.parent]
    return [app for app in spec.cdk_apps if (folder := cdk_app_folder(app)) is not None and path.is_relative_to(folder)]


def cdk_nag_check(apps: Sequence[str]) -> list[Outcome]:
    """Synthesize each CDK app with the cdk-nag packs and report every finding.

    The runner synthesizes each app under the interpreter its `cdk.json` names, so the apps run in
    the repository's own environment whatever interpreter runs this file.

    Args:
        apps: The `cdk.json` paths relative to the tree.

    Returns:
        One outcome, or none when there is no app.

    Raises:
        CheckEnvironmentError: When the cdk-nag runner or its configuration is missing.

    """
    if not apps:
        return []
    missing = [path.name for path in (CDK_NAG_RUNNER, CDK_NAG_CONFIG) if not path.is_file()]
    if missing:
        message = f"{', '.join(missing)} missing from .code-checks: the steward's checks-sync writes them"
        raise CheckEnvironmentError(message)
    return [run_tool("cdk-nag", [sys.executable, str(CDK_NAG_RUNNER), "--config", str(CDK_NAG_CONFIG), *apps])]


# ------------------------------------------------------------------------------------------
# TypeScript and JavaScript


def node_tool(name: str) -> Path | None:
    """Find a Node tool installed in this tree or, for a worktree, in the primary tree.

    Args:
        name: The binary's name under `node_modules/.bin`.

    Returns:
        Its path, or None.

    """
    candidates = (TREE / "node_modules" / ".bin" / name, primary_tree() / "node_modules" / ".bin" / name)
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _cache_root() -> Path:
    """Return the user cache folder for the checks' own toolchains.

    Returns:
        `$XDG_CACHE_HOME/code-checks`, or `~/.cache/code-checks`.

    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "code-checks"


def ephemeral_toolchain(spec: Spec) -> Path:
    """Install, once per pin set, the pinned ESLint and Prettier for a repository with no package.json.

    Args:
        spec: The spec, whose `pins.node` names each package's version.

    Returns:
        The toolchain folder, holding `node_modules`.

    Raises:
        CheckEnvironmentError: When npm is missing or the install fails.

    """
    packages = [f"{name}@{spec.node_pins[name]}" for name in NODE_TOOLCHAIN if name in spec.node_pins]
    key = hashlib.sha256("\n".join(packages).encode()).hexdigest()[:16]
    folder = _cache_root() / f"node-{key}"
    if (folder / "node_modules" / ".bin" / "eslint").exists():
        return folder
    npm = shutil.which("npm")
    if npm is None:
        message = "npm is not on PATH: install Node.js"
        raise CheckEnvironmentError(message)
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="node-", dir=folder.parent))
    (staging / "package.json").write_text('{"private": true}\n', encoding="utf-8")
    install = [npm, "install", "--no-audit", "--no-fund", "--prefix", str(staging), *packages]
    installed = run_tool("npm install", install)
    if not installed.clean:
        shutil.rmtree(staging, ignore_errors=True)
        raise CheckEnvironmentError(installed.output)
    try:
        staging.rename(folder)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
    return folder


def _base_copy(toolchain: Path) -> Path:
    """Place a copy of the repository's ESLint base inside the toolchain, so its imports resolve there.

    Args:
        toolchain: The ephemeral toolchain folder.

    Returns:
        The copy's path.

    """
    text = (TREE / ESLINT_BASE).read_text(encoding="utf-8")
    copy = toolchain / f"eslint.base-{hashlib.sha256(text.encode()).hexdigest()[:16]}.mjs"
    if not copy.exists():
        staging = toolchain / f".{copy.name}.{os.getpid()}"
        staging.write_text(text, encoding="utf-8")
        staging.replace(copy)
    return copy


def eslint_command(stack: str, spec: Spec) -> list[str]:
    """Build the ESLint command: the repository's own ESLint, or the pinned toolchain for JavaScript alone.

    Args:
        stack: `typescript` or `javascript`.
        spec: The spec.

    Returns:
        The command up to the file arguments.

    Raises:
        CheckEnvironmentError: When the repository's Node packages are not installed.

    """
    own = node_tool("eslint")
    if own is not None:
        configured = any(Path(rel).name in ESLINT_OWN_CONFIGS for rel in tracked(CONFIG_SUFFIXES))
        return [str(own), "--no-warn-ignored", *([] if configured else ["--config", ESLINT_BASE])]
    if stack == "typescript" or (TREE / "package.json").is_file():
        message = "ESLint is not installed: run `npm ci`"
        raise CheckEnvironmentError(message)
    toolchain = ephemeral_toolchain(spec)
    eslint = toolchain / "node_modules" / ".bin" / "eslint"
    return [str(eslint), "--no-warn-ignored", "--config", str(_base_copy(toolchain))]


def prettier_command(stack: str, spec: Spec) -> list[str]:
    """Build the Prettier command, resolved like ESLint's.

    Args:
        stack: `typescript` or `javascript`.
        spec: The spec.

    Returns:
        The Prettier binary.

    Raises:
        CheckEnvironmentError: When the repository's Node packages are not installed.

    """
    own = node_tool("prettier")
    if own is not None:
        return [str(own)]
    if stack == "typescript" or (TREE / "package.json").is_file():
        message = "Prettier is not installed: run `npm ci`"
        raise CheckEnvironmentError(message)
    return [str(ephemeral_toolchain(spec) / "node_modules" / ".bin" / "prettier")]


def eslint_check(stack: str, mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run ESLint over the files, or over the tree.

    Args:
        stack: `typescript` or `javascript`.
        mode: `edit`, `commit` or `all`.
        files: The paths to check.
        spec: The spec.

    Returns:
        One outcome.

    """
    targets = ["."] if mode == "all" else list(files)
    return [run_tool(f"eslint ({stack})", [*eslint_command(stack, spec), *targets])]


def prettier_check(stack: str, mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run Prettier: it writes at commit and only checks elsewhere.

    Args:
        stack: `typescript` or `javascript`.
        mode: `edit`, `commit` or `all`.
        files: The paths to check.
        spec: The spec.

    Returns:
        One outcome.

    """
    action = "--write" if mode == "commit" else "--check"
    targets = ["."] if mode == "all" else list(files)
    argv = [*prettier_command(stack, spec), action, "--ignore-unknown", *targets]
    return [run_tool(f"prettier {action} ({stack})", argv)]


def nearest_tsconfig(path: Path) -> Path | None:
    """Find the closest `tsconfig.json` at or above a file, inside the tree.

    Args:
        path: The file.

    Returns:
        The tsconfig, or None.

    """
    folders = [folder for folder in path.parents if folder.is_relative_to(TREE)]
    return next((folder / "tsconfig.json" for folder in folders if (folder / "tsconfig.json").is_file()), None)


def _diagnostics_for(output: str, rel: str) -> str:
    """Keep only the tsc diagnostics that start in one file, with their continuation lines.

    Args:
        output: tsc's output (`--pretty false`).
        rel: The file's path relative to the tree.

    Returns:
        The kept lines.

    """
    kept: list[str] = []
    keeping = False
    for line in output.splitlines():
        keeping = line.startswith(f"{rel}(") or (keeping and line.startswith(" "))
        kept.extend([line] if keeping else [])
    return "\n".join(kept)


def tsc_project(tsconfig: Path) -> Outcome:
    """Type-check one TypeScript project without emitting, incrementally.

    Args:
        tsconfig: The project's tsconfig.

    Returns:
        The outcome.

    Raises:
        CheckEnvironmentError: When TypeScript is not installed.

    """
    tsc = node_tool("tsc")
    if tsc is None:
        message = "TypeScript is not installed: run `npm ci`"
        raise CheckEnvironmentError(message)
    rel = tsconfig.relative_to(TREE).as_posix()
    digest = hashlib.sha256(rel.encode()).hexdigest()[:16]
    info = TREE / "node_modules" / ".cache" / "code-checks" / f"{digest}.tsbuildinfo"
    info.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(tsc), "--noEmit", "--pretty", "false", "--incremental", "--tsBuildInfoFile", str(info), "-p", rel]
    return run_tool(f"tsc -p {rel}", argv)


def tsc_check(mode: str, files: Sequence[str]) -> list[Outcome]:
    """Type-check TypeScript: at edit time the edited file's project, reporting that file only.

    Args:
        mode: `edit`, `commit` or `all`.
        files: The edited file (edit mode).

    Returns:
        The outcomes.

    """
    if mode != "edit":
        return [tsc_project(TREE / rel) for rel in tracked([".json"]) if Path(rel).name == "tsconfig.json"]
    tsconfig = nearest_tsconfig(TREE / files[0])
    if tsconfig is None:
        return []
    outcome = tsc_project(tsconfig)
    own = _diagnostics_for(outcome.output, files[0])
    return [Outcome(outcome.check, clean=not own, output=own)]


# ------------------------------------------------------------------------------------------
# shell


def is_shell_script(path: Path) -> bool:
    """Report whether a file is a shell script, by suffix or by its first line.

    Args:
        path: The file.

    Returns:
        True for `.sh`/`.bash` files and suffix-less files with a sh or bash shebang.

    """
    if path.suffix in SHELL_SUFFIXES:
        return True
    if path.suffix or not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as script:
            first_line = script.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(SHELL_SHEBANG.match(first_line))


def shellcheck_check(mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run shellcheck at its pinned version.

    Args:
        mode: `edit`, `commit` or `all`.
        files: The scripts (all: every tracked shell script).
        spec: The spec.

    Returns:
        One outcome, or none when there is no script.

    """
    candidates = tracked(["", *SHELL_SUFFIXES]) if mode == "all" else []
    scripts = list(files) if mode != "all" else [rel for rel in candidates if is_shell_script(TREE / rel)]
    if not scripts:
        return []
    argv = [_uvx(), "--from", f"shellcheck-py=={spec.pins['shellcheck-py']}", "shellcheck", *scripts]
    return [run_tool("shellcheck", argv)]


# ------------------------------------------------------------------------------------------
# dispatch

type CheckRunner = Callable[[str, Sequence[str], Spec], list[Outcome]]

HOOKS: Final[dict[str, tuple[str, CheckRunner]]] = {
    "python-ruff": ("python", ruff_checks),
    "python-mypy": ("python", mypy_check),
    "cdk-nag": ("cdk", lambda _mode, _files, spec: cdk_nag_check(spec.cdk_apps)),
    "ts-eslint": ("typescript", lambda mode, files, spec: eslint_check("typescript", mode, files, spec)),
    "ts-prettier": ("typescript", lambda mode, files, spec: prettier_check("typescript", mode, files, spec)),
    "ts-tsc": ("typescript", lambda mode, files, _spec: tsc_check(mode, files)),
    "js-eslint": ("javascript", lambda mode, files, spec: eslint_check("javascript", mode, files, spec)),
    "js-prettier": ("javascript", lambda mode, files, spec: prettier_check("javascript", mode, files, spec)),
    "shellcheck": ("shell", shellcheck_check),
}


def edit_hooks(path: Path, spec: Spec) -> list[str]:
    """Choose the checks for one edited file by its type and the repository's stacks.

    Args:
        path: The edited file.
        spec: The spec.

    Returns:
        The hook ids that apply.

    """
    by_kind: list[tuple[bool, list[str]]] = [
        (path.suffix in PYTHON_SUFFIXES, ["python-ruff", "python-mypy"]),
        (path.suffix in TYPESCRIPT_SUFFIXES, ["ts-eslint", "ts-prettier", "ts-tsc"]),
        (path.suffix in JAVASCRIPT_SUFFIXES, ["js-eslint", "js-prettier"]),
        (is_shell_script(path), ["shellcheck"]),
        (bool(apps_for(path, spec)), ["cdk-nag"]),
    ]
    hooks = [hook for applies, ids in by_kind if applies for hook in ids]
    return [hook for hook in hooks if HOOKS[hook][0] in spec.stacks]


def run_hooks(hooks: Sequence[str], mode: str, files: Sequence[str], spec: Spec) -> list[Outcome]:
    """Run hooks side by side and collect their outcomes in hook order.

    Args:
        hooks: The hook ids.
        mode: `edit`, `commit` or `all`.
        files: The files handed to every hook.
        spec: The spec.

    Returns:
        Every outcome.

    """

    def one(hook: str) -> list[Outcome]:
        runner = HOOKS[hook][1]
        return _guarded(hook, lambda: runner(mode, files, spec))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(hooks), 1)) as pool:
        results = list(pool.map(one, hooks))
    return [outcome for outcomes in results for outcome in outcomes]


def run_edit(file_argument: str, spec: Spec) -> list[Outcome]:
    """Check one edited file; the CDK check narrows to the apps the file belongs to.

    Args:
        file_argument: The file, absolute or relative to the working directory.
        spec: The spec.

    Returns:
        The outcomes.

    """
    path = Path(file_argument).resolve()
    if not path.is_file() or not path.is_relative_to(TREE) or path.is_relative_to(CHECKS_DIR):
        return []
    rel = path.relative_to(TREE).as_posix()
    narrowed = dataclasses.replace(spec, cdk_apps=tuple(apps_for(path, spec)))
    return run_hooks(edit_hooks(path, spec), "edit", [rel], narrowed)


def report(outcomes: Iterable[Outcome]) -> int:
    """Print every finding.

    Args:
        outcomes: The outcomes.

    Returns:
        1 when any check found something, else 0.

    """
    failed = [outcome for outcome in outcomes if not outcome.clean]
    sys.stdout.write("".join(f"== {outcome.check} ==\n{outcome.output}\n" for outcome in failed))
    return EXIT_FINDINGS if failed else EXIT_CLEAN


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: The arguments after the program name.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("edit").add_argument("file")
    commit = modes.add_parser("commit")
    commit.add_argument("hook", choices=sorted(HOOKS))
    commit.add_argument("files", nargs="*")
    modes.add_parser("all").add_argument("stacks", nargs="*")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    """Run the requested checks.

    Args:
        argv: The arguments after the program name.

    Returns:
        0 clean, 1 findings, 2 when `checks.json` cannot be read.

    """
    arguments = parse_arguments(argv)
    try:
        spec = load_spec()
    except ValueError as error:
        sys.stdout.write(f"{error}\n")
        return EXIT_USAGE
    if arguments.mode == "edit":
        return report(run_edit(arguments.file, spec))
    if arguments.mode == "commit":
        return report(run_hooks([arguments.hook], "commit", arguments.files, spec))
    stacks = frozenset(arguments.stacks) or spec.stacks
    hooks = [hook for hook, (stack, _runner) in HOOKS.items() if stack in stacks and stack in spec.stacks]
    return report(run_hooks(hooks, "all", [], spec))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
