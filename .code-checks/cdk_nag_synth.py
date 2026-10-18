"""Run cdk-nag on a repository's CDK apps by synthesizing them, without changing the apps.

Usage:
    python cdk_nag_synth.py --config .code-checks/cdk-nag.json [path/to/cdk.json ...]

With no `cdk.json` named, every `cdk.json` git tracks in the repository holding the current
folder is checked. Each app is synthesized in its own process, under the interpreter its
`cdk.json` `app` command names (the repository's own environment), the way the CDK CLI runs it:
the app's context from `cdk.json` and `cdk.context.json`, the cloud assembly in a temporary
folder that is deleted afterwards. Before the app synthesizes, every rule pack the config names
and the app has not added itself is registered with `aws_cdk.Validations`, so the app's own
acknowledgements apply. Nothing is deployed and no AWS credentials are used.

Exit status: 0 when no app has a finding at a level the config fails on, 1 otherwise, and 1 when
an app cannot be synthesized (the reason is printed).

The script uses the standard library only, because it runs in each repository's environment,
on Python 3.12 or later.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import runpy
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs git, uv and Python with list argv and no shell
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from types import ModuleType

CDK_JSON_NAME: Final = "cdk.json"
CDK_CONTEXT_JSON_NAME: Final = "cdk.context.json"
REPORT_FILE_NAME: Final = "policy-validation-report.json"
MANIFEST_FILE_NAME: Final = "manifest.json"
REPORT_JSON_CONTEXT_KEY: Final = "@aws-cdk/core:validationReportJson"
CDK_LIB_DISTRIBUTION: Final = "aws-cdk-lib"
CDK_NAG_DISTRIBUTION: Final = "cdk-nag"
CDK_NAG_MODULE: Final = "cdk_nag"
CDK_MODULE: Final = "aws_cdk"
JSII_ERRORS_MODULE: Final = "jsii.errors"
JSII_ERROR_NAMES: Final = ("JSIIError", "JavaScriptError")
MINIMUM_CDK_LIB: Final = (2, 257)
SUPPORTED_CDK_NAG_MAJOR: Final = 3
MINIMUM_APP_COMMAND_TOKENS: Final = 2
APP_TIMEOUT_SECONDS: Final = 600
OVERLAY_INSTALL_TIMEOUT_SECONDS: Final = 300
CACHE_HOME_VARIABLE: Final = "XDG_CACHE_HOME"
DEFAULT_CACHE_HOME: Final = Path.home() / ".cache"
OVERLAY_CACHE_FOLDER: Final = "code-checks"
ACCOUNT_VARIABLE: Final = "CDK_DEFAULT_ACCOUNT"
REGION_VARIABLE: Final = "CDK_DEFAULT_REGION"
OUTDIR_VARIABLE: Final = "CDK_OUTDIR"
CONTEXT_VARIABLE: Final = "CDK_CONTEXT_JSON"
LEVELS: Final = frozenset({"error", "warning", "info"})
ACKNOWLEDGE_HINT: Final = "acknowledge with Validations.of(<construct>).acknowledge(id=..., reason=...)"
PROGRAM_DESCRIPTION: Final = "Run cdk-nag on CDK apps by synthesizing them."
RESULT_FILE_NAME: Final = "result.txt"
CHILD_OUTPUT_TAIL_LINES: Final = 40
EXIT_CLEAN: Final = 0
EXIT_FINDINGS: Final = 1


class CheckError(Exception):
    """An app could not be checked; the message names the reason and the fix."""


class Plugin(Protocol):
    """A policy validation plugin, as `aws_cdk.Validations` accepts it."""


class ValidationsRegistry(Protocol):
    """The `aws_cdk.Validations` object of one scope."""

    def add_plugins(self, *plugins: Plugin) -> None:
        """Register validation plugins with the scope."""
        ...


class AddPlugins(Protocol):
    """The unbound `aws_cdk.Validations.add_plugins` method."""

    def __call__(self, registry: ValidationsRegistry, /, *plugins: Plugin) -> None:
        """Register validation plugins with a registry's scope."""
        ...


class Synth(Protocol):
    """The unbound `aws_cdk.App.synth` method."""

    def __call__(self, app: object, /, **synth_options: object) -> object:
        """Synthesize the app and return its cloud assembly."""
        ...


type PackFactory = Callable[[object], Plugin]


@dataclass(frozen=True)
class NagConfig:
    """The canonical cdk-nag settings a repository carries."""

    cdk_nag_version: str
    packs: tuple[str, ...]
    fail_on: frozenset[str]
    placeholder_account: str
    placeholder_region: str


@dataclass(frozen=True)
class CdkApp:
    """One CDK app, as its `cdk.json` describes it."""

    folder: Path
    interpreter: str
    script: Path
    context: Mapping[str, object]


@dataclass(frozen=True)
class Finding:
    """One rule violation by one construct."""

    level: str
    rule: str
    construct_path: str
    message: str


@dataclass(frozen=True)
class SynthOutcome:
    """What one synthesis left behind."""

    error: str | None
    report: Path
    manifest: Path


def _require_str(raw: Mapping[str, object], key: str, source: Path) -> str:
    """Return a non-empty string field.

    Args:
        raw: The parsed JSON object.
        key: The field name.
        source: The file the object came from, for the message.

    Returns:
        The field's value.

    Raises:
        CheckError: If the field is missing or not a non-empty string.

    """
    value: object = raw.get(key)
    if not isinstance(value, str) or not value:
        message: str = f"{source}: '{key}' must be a non-empty string"
        raise CheckError(message)
    return value


def _require_str_list(raw: Mapping[str, object], key: str, source: Path) -> tuple[str, ...]:
    """Return a list-of-strings field as a tuple.

    Args:
        raw: The parsed JSON object.
        key: The field name.
        source: The file the object came from, for the message.

    Returns:
        The field's strings.

    Raises:
        CheckError: If the field is missing or not a list of non-empty strings.

    """
    value: object = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        message: str = f"{source}: '{key}' must be a list of non-empty strings"
        raise CheckError(message)
    return tuple(str(item) for item in value)


def _read_json_object(path: Path) -> dict[str, object]:
    """Read a JSON file that must hold an object.

    Args:
        path: The JSON file.

    Returns:
        The object, with string keys.

    Raises:
        CheckError: If the file cannot be read or does not hold a JSON object.

    """
    try:
        with path.open(encoding="utf-8") as json_file:
            parsed: object = json.load(json_file)
    except (OSError, json.JSONDecodeError) as error:
        message: str = f"reading {path} failed: {error}"
        raise CheckError(message) from error
    if not isinstance(parsed, dict):
        message = f"{path} must hold a JSON object"
        raise CheckError(message)
    return {str(key): value for key, value in parsed.items()}


def load_config(config_path: Path) -> NagConfig:
    """Load and validate the cdk-nag config.

    Args:
        config_path: The repository's `cdk-nag.json`.

    Returns:
        The validated config.

    Raises:
        CheckError: If a field is malformed.

    """
    raw: dict[str, object] = _read_json_object(config_path)
    fail_on: tuple[str, ...] = _require_str_list(raw, "fail_on", config_path)
    unknown_levels: set[str] = set(fail_on) - LEVELS
    if unknown_levels:
        message: str = f"{config_path}: unknown levels in 'fail_on': {sorted(unknown_levels)}"
        raise CheckError(message)
    placeholder: object = raw.get("placeholder_env")
    if not isinstance(placeholder, dict):
        message = f"{config_path}: 'placeholder_env' must be an object"
        raise CheckError(message)
    placeholder_env: dict[str, object] = {str(key): value for key, value in placeholder.items()}
    return NagConfig(
        cdk_nag_version=_require_str(raw, "cdk_nag", config_path),
        packs=_require_str_list(raw, "packs", config_path),
        fail_on=frozenset(fail_on),
        placeholder_account=_require_str(placeholder_env, "account", config_path),
        placeholder_region=_require_str(placeholder_env, "region", config_path),
    )


def _git(folder: Path, *arguments: str) -> str:
    """Run git in a folder and return its standard output.

    Args:
        folder: The folder git runs in.
        *arguments: The git arguments.

    Returns:
        Git's standard output.

    Raises:
        CheckError: If git fails.

    """
    completed: subprocess.CompletedProcess[str] = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - list argv, no shell
        ["git", "-C", str(folder), *arguments],  # ruff: ignore[start-process-with-partial-path] - git from PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message: str = f"git {' '.join(arguments)} in {folder} failed: {completed.stderr.strip()}"
        raise CheckError(message)
    return completed.stdout


def tracked_cdk_json_files(folder: Path) -> list[Path]:
    """Return every `cdk.json` git tracks in the repository holding a folder.

    Args:
        folder: Any folder inside the repository.

    Returns:
        The absolute paths, sorted.

    """
    top_level: Path = Path(_git(folder, "rev-parse", "--show-toplevel").strip())
    listing: str = _git(top_level, "ls-files", "-z", "--", f":(glob)**/{CDK_JSON_NAME}")
    return sorted(top_level / relative for relative in listing.split("\0") if relative)


def _primary_tree(folder: Path) -> Path | None:
    """Return the primary working tree of the repository holding a folder.

    Args:
        folder: Any folder inside the repository.

    Returns:
        The primary working tree, or None outside a repository.

    """
    try:
        common_dir: str = _git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except CheckError:
        return None
    return Path(common_dir).parent


def _relative_to_tree(path: Path) -> Path | None:
    """Return a path relative to the top level of its working tree.

    Args:
        path: A file inside a working tree.

    Returns:
        The relative path, or None outside a repository.

    """
    try:
        top_level: Path = Path(_git(path.parent, "rev-parse", "--show-toplevel").strip())
    except CheckError:
        return None
    return path.relative_to(top_level)


def _resolve_interpreter(cdk_json: Path, interpreter: str) -> str:
    """Resolve the interpreter an app command names to an existing executable.

    A command naming a bare interpreter (`python3`) runs under the interpreter running this
    script. A path is resolved against the `cdk.json` folder and, in a worktree that has no
    environment of its own, against the same place in the primary working tree.

    Args:
        cdk_json: The app's `cdk.json`.
        interpreter: The first token of the app command.

    Returns:
        The interpreter to run the app under.

    Raises:
        CheckError: If the named environment exists in neither tree.

    """
    if "/" not in interpreter:
        return sys.executable
    candidate: Path = Path(os.path.normpath(cdk_json.parent / interpreter))
    if candidate.is_file():
        return str(candidate)
    primary: Path | None = _primary_tree(cdk_json.parent)
    relative_cdk_json: Path | None = _relative_to_tree(cdk_json)
    fallback: Path | None = None
    if primary is not None and relative_cdk_json is not None:
        fallback = Path(os.path.normpath(primary / relative_cdk_json.parent / interpreter))
    if fallback is not None and fallback.is_file():
        return str(fallback)
    message: str = f"{cdk_json}: no environment at {interpreter}: run `task install`"
    raise CheckError(message)


def read_app(cdk_json: Path) -> CdkApp:
    """Read a CDK app's command and context the way the CDK CLI does.

    Args:
        cdk_json: The app's `cdk.json`.

    Returns:
        The app, with the context of `cdk.json` and `cdk.context.json` merged.

    Raises:
        CheckError: If the file is malformed or the app is not a Python script.

    """
    raw: dict[str, object] = _read_json_object(cdk_json)
    command: list[str] = shlex.split(_require_str(raw, "app", cdk_json))
    if len(command) < MINIMUM_APP_COMMAND_TOKENS or not command[-1].endswith(".py"):
        message: str = f"{cdk_json}: app command is not `<python> <script>.py`; only Python apps are checked"
        raise CheckError(message)
    context_value: object = raw.get("context", {})
    if not isinstance(context_value, dict):
        message = f"{cdk_json}: 'context' must be an object"
        raise CheckError(message)
    context: dict[str, object] = {str(key): value for key, value in context_value.items()}
    context_file: Path = cdk_json.parent / CDK_CONTEXT_JSON_NAME
    if context_file.is_file():
        context.update(_read_json_object(context_file))
    context[REPORT_JSON_CONTEXT_KEY] = True
    return CdkApp(
        folder=cdk_json.parent.resolve(),
        interpreter=command[0],
        script=(cdk_json.parent / command[-1]).resolve(),
        context=context,
    )


def _version_tuple(version: str) -> tuple[int, ...]:
    """Return the leading numeric release parts of a version string.

    Args:
        version: A version such as `2.262.1` or `3.0.2rc1`.

    Returns:
        The numeric parts up to the first part that is not a plain number.

    """
    parts: list[str] = version.split(".")
    numeric_count: int = next((index for index, part in enumerate(parts) if not part.isdigit()), len(parts))
    return tuple(int(part) for part in parts[:numeric_count])


def _installed_version(distribution: str) -> str | None:
    """Return the installed version of a distribution.

    Args:
        distribution: The distribution name.

    Returns:
        The version, or None when the distribution is not installed.

    """
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _overlay_folder(config: NagConfig) -> Path:
    """Return the cache folder of the cdk-nag overlay at the pinned version.

    Args:
        config: The cdk-nag config.

    Returns:
        The folder the overlay is installed into.

    """
    cache_home: Path = Path(os.environ.get(CACHE_HOME_VARIABLE) or DEFAULT_CACHE_HOME)
    return cache_home / OVERLAY_CACHE_FOLDER / f"{CDK_NAG_DISTRIBUTION}-{config.cdk_nag_version}"


def _run_overlay_install(command: Sequence[str], requirement: str) -> None:
    """Run the overlay install command.

    Args:
        command: The uv command line.
        requirement: The requirement installed, for messages.

    Raises:
        CheckError: If the install fails or times out.

    """
    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - list argv, no shell
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=OVERLAY_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        message: str = f"installing {requirement} timed out after {OVERLAY_INSTALL_TIMEOUT_SECONDS} s"
        raise CheckError(message) from error
    if completed.returncode != 0:
        message = f"installing {requirement} failed: {completed.stderr.strip()}"
        raise CheckError(message)


def _install_overlay(overlay: Path, config: NagConfig) -> None:
    """Install cdk-nag without dependencies into the overlay folder.

    The install goes to a staging folder that is renamed into place, so concurrent checks never
    see a half-installed overlay; the loser of a race discards its copy.

    Args:
        overlay: The overlay folder.
        config: The cdk-nag config.

    Raises:
        CheckError: If uv is missing or the overlay is not in place afterwards.

    """
    uv_path: str | None = shutil.which("uv")
    if uv_path is None:
        message: str = "cdk-nag is not installed and uv, which installs the cached copy, is not on PATH"
        raise CheckError(message)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    staging: Path = Path(tempfile.mkdtemp(prefix=f"{overlay.name}.", dir=overlay.parent))
    requirement: str = f"{CDK_NAG_DISTRIBUTION}=={config.cdk_nag_version}"
    install_command: list[str] = [
        uv_path, "pip", "install", "--quiet", "--no-deps", "--python", sys.executable,
        "--target", str(staging), requirement,
    ]  # fmt: skip
    try:
        _run_overlay_install(install_command, requirement)
        _move_into_place(staging, overlay)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _move_into_place(staging: Path, overlay: Path) -> None:
    """Rename the staging folder to the overlay folder.

    Args:
        staging: The folder the overlay was installed into.
        overlay: The overlay folder.

    Raises:
        CheckError: If the rename fails and no concurrent check put the overlay in place.

    """
    try:
        staging.rename(overlay)
    except OSError as error:
        if overlay.is_dir():
            return
        message: str = f"moving the cdk-nag overlay into {overlay} failed: {error}"
        raise CheckError(message) from error


def ensure_cdk_nag(config: NagConfig) -> None:
    """Make cdk-nag 3 importable: the environment's own, or the cached overlay.

    Args:
        config: The cdk-nag config.

    Raises:
        CheckError: If the environment's CDK or cdk-nag cannot run the cdk-nag 3 packs.

    """
    cdk_lib_version: str | None = _installed_version(CDK_LIB_DISTRIBUTION)
    if cdk_lib_version is None:
        message: str = f"{CDK_LIB_DISTRIBUTION} is not installed in {sys.executable}: run `task install`"
        raise CheckError(message)
    if _version_tuple(cdk_lib_version)[:2] < MINIMUM_CDK_LIB:
        minimum: str = ".".join(str(part) for part in MINIMUM_CDK_LIB)
        message = f"{CDK_LIB_DISTRIBUTION} {cdk_lib_version} is older than {minimum}, which cdk-nag 3 needs"
        raise CheckError(message)
    cdk_nag_version: str | None = _installed_version(CDK_NAG_DISTRIBUTION)
    if cdk_nag_version is not None and _version_tuple(cdk_nag_version)[:1] != (SUPPORTED_CDK_NAG_MAJOR,):
        message = (
            f"{CDK_NAG_DISTRIBUTION} {cdk_nag_version} is installed; the app must move to cdk-nag "
            f"{SUPPORTED_CDK_NAG_MAJOR} and its aws_cdk.Validations API"
        )
        raise CheckError(message)
    if cdk_nag_version is not None:
        return
    overlay: Path = _overlay_folder(config)
    if not overlay.is_dir():
        _install_overlay(overlay, config)
    sys.path.append(str(overlay))


def _load_pack_factories(cdk_nag: ModuleType, packs: Sequence[str]) -> dict[str, PackFactory]:
    """Return the pack classes the config names.

    Args:
        cdk_nag: The `cdk_nag` module.
        packs: The configured pack class names.

    Returns:
        Each pack's class, by name.

    Raises:
        CheckError: If cdk-nag has no pack of a configured name.

    """
    missing_packs: list[str] = [pack for pack in packs if not callable(getattr(cdk_nag, pack, None))]
    if missing_packs:
        message: str = f"cdk-nag has no rule packs named {missing_packs}"
        raise CheckError(message)
    return {pack: getattr(cdk_nag, pack) for pack in packs}


def install_packs(cdk: ModuleType, cdk_nag: ModuleType, packs: Sequence[str]) -> None:
    """Make every app synthesize with the configured packs it has not added itself.

    `Validations.add_plugins` is wrapped to record the packs the app adds, and `App.synth`
    to add the remaining configured packs just before the app synthesizes, so a pack the app
    adds itself is never registered twice.

    Args:
        cdk: The `aws_cdk` module.
        cdk_nag: The `cdk_nag` module.
        packs: The configured pack class names.

    """
    factories: dict[str, PackFactory] = _load_pack_factories(cdk_nag, packs)
    added_packs: set[str] = set()
    original_add_plugins: AddPlugins = cdk.Validations.add_plugins
    original_synth: Synth = cdk.App.synth

    def recording_add_plugins(registry: ValidationsRegistry, *plugins: Plugin) -> None:
        """Record the class of every plugin the app registers, then register them."""
        added_packs.update(type(plugin).__name__ for plugin in plugins)
        original_add_plugins(registry, *plugins)

    def synth_with_packs(app: object, **synth_options: object) -> object:
        """Register the configured packs the app did not add, then synthesize.

        Returns:
            What the app's own synth returns.

        """
        remaining: list[Plugin] = [factory(app) for pack, factory in factories.items() if pack not in added_packs]
        added_packs.update(factories)
        if remaining:
            original_add_plugins(cdk.Validations.of(app), *remaining)
        return original_synth(app, **synth_options)

    cdk.Validations.add_plugins = recording_add_plugins
    cdk.App.synth = synth_with_packs


def _synth_errors() -> tuple[type[BaseException], ...]:
    """Return the exception classes a failed synthesis raises through jsii.

    The jsii process provider raises `RuntimeError` for an error thrown in the JavaScript
    runtime (a `ValidationFailed` included); the kernel's own faults are jsii's error classes.

    Returns:
        `RuntimeError` and the exception classes jsii defines under the expected names.

    """
    jsii_errors: ModuleType = importlib.import_module(JSII_ERRORS_MODULE)
    candidates: list[object] = [getattr(jsii_errors, name, None) for name in JSII_ERROR_NAMES]
    jsii_classes: tuple[type[BaseException], ...] = tuple(
        candidate for candidate in candidates if isinstance(candidate, type) and issubclass(candidate, BaseException)
    )
    return (RuntimeError, *jsii_classes)


def _set_environment(app: CdkApp, config: NagConfig, outdir: Path) -> None:
    """Set what the CDK CLI gives an app, before the jsii runtime starts and inherits it.

    Args:
        app: The app.
        config: The cdk-nag config, for the placeholder account and region.
        outdir: The cloud assembly folder.

    """
    os.environ[OUTDIR_VARIABLE] = str(outdir)
    os.environ[CONTEXT_VARIABLE] = json.dumps(app.context)
    os.environ.setdefault(ACCOUNT_VARIABLE, config.placeholder_account)
    os.environ.setdefault(REGION_VARIABLE, config.placeholder_region)


def synthesize(app: CdkApp, config: NagConfig, outdir: Path) -> SynthOutcome:
    """Synthesize one app in this process with the configured packs.

    The working folder becomes the `cdk.json` folder before `aws_cdk` is imported, because the
    jsii runtime starts at import and resolves the app's relative asset paths against it.

    Args:
        app: The app to synthesize.
        config: The cdk-nag config.
        outdir: The temporary cloud assembly folder.

    Returns:
        The synthesis error message, if any, and where the report and manifest are.

    """
    _set_environment(app, config, outdir)
    os.chdir(app.folder)
    sys.path.insert(0, str(app.script.parent))
    sys.argv = [str(app.script)]
    ensure_cdk_nag(config)
    install_packs(importlib.import_module(CDK_MODULE), importlib.import_module(CDK_NAG_MODULE), config.packs)
    synth_errors: tuple[type[BaseException], ...] = _synth_errors()
    error_text: str | None = None
    try:
        runpy.run_path(str(app.script), run_name="__main__")
    except synth_errors as error:
        error_text = str(error).strip() or type(error).__name__
    return SynthOutcome(error=error_text, report=outdir / REPORT_FILE_NAME, manifest=outdir / MANIFEST_FILE_NAME)


def _as_objects(value: object) -> Iterator[Mapping[str, object]]:
    """Yield the JSON objects in a JSON list, skipping anything else.

    Args:
        value: A parsed JSON value.

    Yields:
        Each object in the list, with string keys.

    """
    items: list[object] = value if isinstance(value, list) else []
    yield from ({str(key): item_value for key, item_value in item.items()} for item in items if isinstance(item, dict))


def _violation_findings(violation: Mapping[str, object]) -> list[Finding]:
    """Return one finding per construct a report violation names.

    Args:
        violation: One violation of the policy validation report.

    Returns:
        The findings.

    """
    level: str = str(violation.get("severity", "error")).lower()
    rule: str = str(violation.get("ruleName", "unknown-rule"))
    message: str = " ".join(str(violation.get("description", "")).split())
    constructs: list[Mapping[str, object]] = list(_as_objects(violation.get("violatingConstructs")))
    paths: list[str] = [str(construct.get("constructPath", "")) for construct in constructs] or [""]
    return [Finding(level=level, rule=rule, construct_path=path, message=message) for path in paths]


def read_findings(report: Path) -> list[Finding]:
    """Read every finding in a policy validation report.

    Args:
        report: The `policy-validation-report.json` file.

    Returns:
        The findings, empty when the file does not exist.

    """
    if not report.is_file():
        return []
    raw: dict[str, object] = _read_json_object(report)
    plugin_reports: list[Mapping[str, object]] = list(_as_objects(raw.get("pluginReports")))
    violations: list[Mapping[str, object]] = [
        violation for plugin_report in plugin_reports for violation in _as_objects(plugin_report.get("violations"))
    ]
    return [finding for violation in violations for finding in _violation_findings(violation)]


def missing_context(manifest: Path) -> bool:
    """Report whether the cloud assembly records context lookups that were not answered.

    Args:
        manifest: The cloud assembly's `manifest.json`.

    Returns:
        True when the manifest lists missing context.

    """
    if not manifest.is_file():
        return False
    return bool(_read_json_object(manifest).get("missing"))


def _format_finding(name: str, finding: Finding) -> str:
    """Return a finding as one report line.

    Args:
        name: The app's `cdk.json`, as shown.
        finding: The finding.

    Returns:
        The line, newline included.

    """
    return (
        f"{name}: {finding.level.upper()} {finding.rule} {finding.construct_path}: "
        f"{finding.message}; {ACKNOWLEDGE_HINT}\n"
    )


def _display_name(cdk_json: Path) -> str:
    """Return a `cdk.json` path relative to its repository, for messages.

    Args:
        cdk_json: The app's `cdk.json`.

    Returns:
        The relative path, or the path as given outside a repository.

    """
    relative: Path | None = _relative_to_tree(cdk_json)
    return str(relative if relative is not None else cdk_json)


def check_app_in_process(cdk_json: Path, config: NagConfig) -> tuple[int, str]:
    """Synthesize one app here and report its findings.

    Args:
        cdk_json: The app's `cdk.json`.
        config: The cdk-nag config.

    Returns:
        The exit status and the report: one line per finding at a level the config fails on.

    Raises:
        CheckError: If the app cannot be synthesized for a reason other than a finding.

    """
    name: str = _display_name(cdk_json)
    app: CdkApp = read_app(cdk_json)
    with tempfile.TemporaryDirectory(prefix="cdk-nag-") as outdir_name:
        outcome: SynthOutcome = synthesize(app, config, Path(outdir_name))
        findings: list[Finding] = read_findings(outcome.report)
        lookups_missing: bool = missing_context(outcome.manifest)
    if not findings and outcome.error is not None:
        message: str = f"{name}: synthesis failed: {outcome.error.splitlines()[0]}"
        raise CheckError(message)
    if lookups_missing:
        message = f"{name}: the app looks up context at synth; commit {CDK_CONTEXT_JSON_NAME} for deterministic synth"
        raise CheckError(message)
    failing: list[Finding] = [finding for finding in findings if finding.level in config.fail_on]
    report_text: str = "".join(_format_finding(name, finding) for finding in failing)
    return (EXIT_FINDINGS if failing else EXIT_CLEAN), report_text


def _run_in_process(cdk_json: Path, config: NagConfig, result_path: Path) -> int:
    """Check one app in this process and write its report to the result file.

    The app's own output (the CDK's human-readable validation report included) goes to this
    process's standard streams, which the parent captures; the result file holds only the
    report lines, or the reason the app could not be checked.

    Args:
        cdk_json: The app's `cdk.json`.
        config: The cdk-nag config.
        result_path: The file the parent reads the report from.

    Returns:
        The exit status.

    """
    try:
        status, report_text = check_app_in_process(cdk_json, config)
    except CheckError as error:
        status, report_text = EXIT_FINDINGS, f"{error}\n"
    with result_path.open("w", encoding="utf-8") as result_file:
        result_file.write(report_text)
    return status


def _child_output_tail(completed: subprocess.CompletedProcess[str]) -> str:
    """Return the last lines a child printed, for a child that failed without a report.

    Args:
        completed: The finished child process.

    Returns:
        The tail of its standard output and standard error.

    """
    child_lines: list[str] = f"{completed.stdout}{completed.stderr}".splitlines()
    return "".join(f"{line}\n" for line in child_lines[-CHILD_OUTPUT_TAIL_LINES:])


def check_app_in_child(cdk_json: Path, config_path: Path) -> int:
    """Check one app in a child process under the interpreter its `cdk.json` names.

    The child's own output is captured; its report lines go to standard output, and its
    output tail to standard error only when it failed without a report.

    Args:
        cdk_json: The app's `cdk.json`.
        config_path: The cdk-nag config file.

    Returns:
        The child's exit status.

    Raises:
        CheckError: If the check times out.

    """
    name: str = _display_name(cdk_json)
    interpreter: str = _resolve_interpreter(cdk_json, read_app(cdk_json).interpreter)
    script: str = str(Path(__file__).resolve())
    started: float = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cdk-nag-result-") as result_folder:
        result_path: Path = Path(result_folder) / RESULT_FILE_NAME
        command: list[str] = [
            interpreter, script, "--config", str(config_path), "--app", str(cdk_json), "--result", str(result_path),
        ]  # fmt: skip
        try:
            completed: subprocess.CompletedProcess[str] = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - list argv, no shell
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=APP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            message: str = f"{name}: cdk-nag synth did not finish in {APP_TIMEOUT_SECONDS} s"
            raise CheckError(message) from error
        report_text: str = result_path.read_text(encoding="utf-8") if result_path.is_file() else ""
    sys.stdout.write(report_text)
    if completed.returncode != EXIT_CLEAN and not report_text:
        sys.stderr.write(_child_output_tail(completed))
    sys.stderr.write(f"{name}: cdk-nag synth took {time.monotonic() - started:.2f} s\n")
    return completed.returncode


def _check_one(cdk_json: Path, config_path: Path) -> int:
    """Check one app in a child process, reporting a failure to check as a failed check.

    Args:
        cdk_json: The app's `cdk.json`.
        config_path: The cdk-nag config file.

    Returns:
        The exit status.

    """
    try:
        return check_app_in_child(cdk_json.resolve(), config_path)
    except CheckError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FINDINGS


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: The command-line arguments, without the program name.

    Returns:
        The parsed arguments.

    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=PROGRAM_DESCRIPTION)
    parser.add_argument("--config", type=Path, required=True, help="the repository's cdk-nag.json")
    parser.add_argument("--app", type=Path, help="synthesize this one cdk.json in this process")
    parser.add_argument("--result", type=Path, help="with --app: the file the report is written to")
    parser.add_argument("cdk_json", type=Path, nargs="*", help="cdk.json files; default: every tracked one")
    return parser.parse_args(argv)


def _run(arguments: argparse.Namespace) -> int:
    """Run the check the parsed arguments ask for.

    Args:
        arguments: The parsed arguments.

    Returns:
        The exit status.

    """
    config_path: Path = Path(arguments.config).resolve()
    config: NagConfig = load_config(config_path)
    if arguments.app is not None and arguments.result is not None:
        return _run_in_process(Path(arguments.app).resolve(), config, Path(arguments.result))
    named_files: list[Path] = [Path(path) for path in arguments.cdk_json]
    cdk_json_files: list[Path] = named_files or tracked_cdk_json_files(Path.cwd())
    return max((_check_one(cdk_json, config_path) for cdk_json in cdk_json_files), default=EXIT_CLEAN)


def main(argv: Sequence[str]) -> int:
    """Run the check and turn a failure to check into a message and a failed status.

    Args:
        argv: The command-line arguments, without the program name.

    Returns:
        The exit status.

    """
    try:
        return _run(_parse_arguments(argv))
    except CheckError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FINDINGS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
