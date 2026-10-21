#!/usr/bin/env bash
# PreToolUse hook: block Edit, Write, MultiEdit and NotebookEdit on the
# SkillSpoke matrix (any file named matrix.json under ops/sdlc-automation/,
# in the primary working tree or any worktree).
#
# Exit 0 = approve
# Exit 2 = block
#
# The matrix changes only through `atw-matrix`, which enforces each agent's
# role, the matrix lock and entry checks. The target path is checked, not the
# session's working directory, so a session started in any repository is
# blocked from editing the matrix by absolute path.

set -euo pipefail

input=$(cat)

read -r -d '' checker <<'PY' || true
import json, os, sys

data = json.load(sys.stdin)
tool_input = data.get("tool_input") or {}
path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
if not path:
    sys.exit(0)
if not os.path.isabs(path):
    path = os.path.join(data.get("cwd") or os.getcwd(), path)
resolved = os.path.realpath(path)
for candidate in (os.path.normpath(path), resolved):
    parts = candidate.split(os.sep)
    if parts[-1] != "matrix.json":
        continue
    for i in range(len(parts) - 2):
        if parts[i] == "ops" and parts[i + 1] == "sdlc-automation":
            sys.exit(2)
sys.exit(0)
PY

status=0
printf '%s' "$input" | python3 -c "$checker" || status=$?

if [ "$status" -eq 0 ]; then
  exit 0
fi
if [ "$status" -ne 2 ]; then
  echo "matrix-edit-blocker: checker failed with status $status; blocking to stay safe." >&2
fi

cat >&2 <<'MSG'
BLOCKED: The matrix (ops/sdlc-automation/matrix.json) is changed only through `atw-matrix`.

`atw-matrix` enforces each agent's role, the matrix lock and the entry checks;
an Edit or Write to the file bypasses all three. Make the change with the
`atw-matrix` command that fits it. `atw-matrix --help` lists the commands.
MSG
exit 2
