#!/usr/bin/env bash
# PreToolUse hook: block any Bash command that runs keeper.py or supervisor.py.
#
# Exit 0 = approve
# Exit 2 = block
#
# Only the owner runs the SDLC pipelines; their real runs are the test of the
# pipeline. A session or agent never runs, dry-runs or rehearses them.
#
# Only EXECUTING a script is blocked. A command that merely names one (pgrep,
# ps, grep, rg, git log, cat, ruff, an editor, echo) passes. The command is
# tokenized with shell quoting rules, split into simple commands, and each
# simple command is checked for an execution form:
#   python/python3/pypy [flags] path/keeper.py   python -m [pkg.]keeper
#   uv run [flags] (python ... | path/keeper.py | -m keeper)
#   ./keeper.py, ops/x/supervisor.py, $ROOT/.../keeper.py as the command
#   bash/sh/zsh -c '<one of those>'
#   any of those behind env, nohup, timeout, nice, exec, time, command, sudo,
#   caffeinate, xargs, or VAR=value assignments
#   launchctl or osascript naming either script
# A command the tokenizer cannot parse falls back to a text match.

set -euo pipefail

input=$(cat)

read -r -d '' checker <<'PY' || true
import json, os, re, shlex, sys

NAMES = ("keeper", "supervisor")
SCRIPT_RE = re.compile(r"(^|/)(keeper|supervisor)\.py$")
MENTION_RE = re.compile(r"(keeper|supervisor)\.py")
PY_RE = re.compile(r"^(python[0-9.]*|pypy[0-9.]*)$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SEPARATORS = {";", ";;", "&", "&&", "|", "||", "|&", "(", ")", "`", "\n"}
REDIRECTS = {"<", ">", ">>", "<<", "<<<", ">&", "<&", "&>", "&>>", ">|"}
PY_ARG_FLAGS = {"-W", "-X", "-Q"}
UV_ARG_FLAGS = {
    "--with", "--with-requirements", "--with-editable", "--python", "-p",
    "--project", "--directory", "--env-file", "--package", "--extra",
    "--group", "--index", "--index-url", "--extra-index-url", "--find-links",
    "-f", "--config-file", "--cache-dir", "--refresh-package",
}
WRAPPERS = {"nohup", "exec", "time", "command", "builtin", "caffeinate", "stdbuf"}


def base(tok):
    return tok.rsplit("/", 1)[-1]


def is_script(tok):
    return bool(SCRIPT_RE.search(tok))


def is_module(tok):
    return tok.rsplit(".", 1)[-1] in NAMES


def tokenize(text):
    lex = shlex.shlex(text.replace("\n", ";"), posix=True, punctuation_chars="();<>|&`")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def segments(tokens):
    seg = []
    for tok in tokens:
        if tok in SEPARATORS or tok == "\n":
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def strip_redirects(seg):
    out, skip = [], False
    for tok in seg:
        if skip:
            skip = False
            continue
        if tok in REDIRECTS or re.fullmatch(r"[0-9]*[<>]+&?", tok):
            skip = True
            continue
        out.append(tok)
    return out


def check_python(args):
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "-m":
            return i + 1 < len(args) and is_module(args[i + 1])
        if tok.startswith("-m") and len(tok) > 2:
            return is_module(tok[2:])
        if tok == "-c":
            return i + 1 < len(args) and check_text(args[i + 1])
        if tok in PY_ARG_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return is_script(tok)
    return False


def check_uv(args):
    if not args or args[0] != "run":
        return False
    rest = args[1:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("-m", "--module"):
            return i + 1 < len(rest) and is_module(rest[i + 1])
        if tok == "--":
            return check_command(rest[i + 1:])
        if tok in UV_ARG_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return check_command(rest[i:])
    return False


def check_shell(args):
    for i, tok in enumerate(args):
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            return i + 1 < len(args) and check_text(args[i + 1])
        if not tok.startswith("-"):
            return is_script(tok)
    return False


def skip_wrapper(seg):
    """Return the tokens after leading wrappers and assignments."""
    while seg:
        head = seg[0]
        name = base(head)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):
            seg = seg[1:]
        elif name in WRAPPERS:
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                seg = seg[1:]
        elif name == "env":
            seg = seg[1:]
            while seg and (seg[0].startswith("-") or "=" in seg[0]):
                if seg[0] in ("-u", "-C", "-S", "--unset", "--chdir"):
                    seg = seg[1:]
                seg = seg[1:]
        elif name == "sudo":
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                if seg[0] in ("-u", "-g", "-C", "-D", "-h", "-p", "-U"):
                    seg = seg[1:]
                seg = seg[1:]
        elif name in ("timeout", "gtimeout"):
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                if seg[0] in ("-s", "-k", "--signal", "--kill-after"):
                    seg = seg[1:]
                seg = seg[1:]
            seg = seg[1:]  # the duration
        elif name == "nice":
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                if seg[0] == "-n":
                    seg = seg[1:]
                seg = seg[1:]
        elif name == "xargs":
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                if seg[0] in ("-n", "-I", "-L", "-P", "-s", "-d", "-E"):
                    seg = seg[1:]
                seg = seg[1:]
        else:
            return seg
    return seg


def check_command(seg):
    seg = skip_wrapper(strip_redirects(seg))
    if not seg:
        return False
    head, args = seg[0], seg[1:]
    name = base(head)
    if is_script(head):
        return True
    if PY_RE.match(name):
        return check_python(args)
    if name == "uv":
        return check_uv(args)
    if name == "uvx":
        return check_command(args)
    if name in SHELLS:
        return check_shell(args)
    if name in ("launchctl", "osascript", "open"):
        return any(MENTION_RE.search(a) for a in args)
    if name in ("eval", "source", "."):
        return check_text(" ".join(args)) or any(is_script(a) for a in args)
    return False


def check_text(text):
    try:
        tokens = tokenize(text)
    except ValueError:
        return fallback(text)
    for tok in tokens:
        for inner in re.findall(r"\$\((.*)\)", tok):
            if check_text(inner):
                return True
    return any(check_command(seg) for seg in segments(tokens))


def fallback(text):
    if not MENTION_RE.search(text):
        return False
    ws, tok = r"[ \t]", r"[^ \t\n]"
    script = rf"({tok}*/)?(keeper|supervisor)\.py({ws}|$|\))"
    pats = [
        rf"(^|[ \t/(])(python[0-9.]*|pypy3?)({ws}+-{tok}+)*{ws}+{script}",
        rf"(^|[ \t/(])(python[0-9.]*|pypy3?)({ws}+-{tok}+)*{ws}+-m{ws}+({tok}+\.)?(keeper|supervisor)({ws}|$)",
        rf"(^|[ \t/(])uv{ws}+run({ws}+{tok}+)*{ws}+{script}",
        rf"^{ws}*((env|exec|nohup|time|command|timeout{ws}+{tok}+)({ws}+|$)|[A-Za-z_][A-Za-z0-9_]*={tok}*{ws}+)*{script}",
        r"(launchctl|osascript)",
    ]
    flat = text.replace('"', "").replace("'", "")
    for line in re.split(r"[;&|\n]", flat):
        if any(re.search(p, line) for p in pats):
            return True
    return False


command = json.load(sys.stdin).get("tool_input", {}).get("command") or ""
sys.exit(2 if command and check_text(command) else 0)
PY

status=0
printf '%s' "$input" | python3 -c "$checker" || status=$?

if [ "$status" -eq 0 ]; then
  exit 0
fi
if [ "$status" -ne 2 ]; then
  echo "pipeline-run-blocker: checker failed with status $status; blocking to stay safe." >&2
fi

cat >&2 <<'MSG'
BLOCKED: Only the owner runs `keeper.py` and `supervisor.py`; their real runs are the test of the pipeline.

No session or agent runs them, pre-tests them, or rehearses them: no dry runs,
no runs on scratch copies, no apply:false or replay runs, no stub-dispatcher
runs, and no dispatching workflows or plugin commands as a test. Allowed: fast
unit tests with real testing value, and existing suites as regression checks.
MSG
exit 2
