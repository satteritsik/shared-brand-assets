#!/usr/bin/env bash
# Run GitNexus at the one version this project pins.
#
# The version is defined once, in the sibling file `gitnexus-version`. Every
# route that runs GitNexus for this project goes through this script: the fleet
# indexer, the single-repository indexer, the Taskfile tasks and the
# per-repository `.gitnexus/run.cjs` runner.
#
# The index format changes between GitNexus releases, so a reader and a writer
# on different versions cannot share an index. The reader is the user-level
# MCP server, which runs the `gitnexus` installed on PATH. This script runs
# that same binary, and only when it reports the pinned version; otherwise it
# stops and names the install command, so no index is written by a version the
# MCP server cannot read.
#
# Usage: gitnexus-pinned.sh <gitnexus arguments...>
# Exit:  2 when the version file is missing or malformed, 127 when no
#        `gitnexus` of the pinned version is on PATH, else GitNexus's own status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${SCRIPT_DIR}/gitnexus-version"

if [ ! -r "$VERSION_FILE" ]; then
    echo "gitnexus-pinned: version file not readable: ${VERSION_FILE}" >&2
    exit 2
fi

PINNED="$(tr -d '[:space:]' <"$VERSION_FILE")"
if ! [[ "$PINNED" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "gitnexus-pinned: ${VERSION_FILE} must hold one exact version (MAJOR.MINOR.PATCH), found '${PINNED}'" >&2
    exit 2
fi

INSTALLED_BIN="$(command -v gitnexus || true)"
# `npm i -g` under nvm installs into the default Node's bin, which only an
# interactive shell puts on PATH. A git hook or any scheduled, non-interactive
# job runs without it, so nvm is loaded here to put the default Node, and the
# gitnexus installed with it, on PATH.
NVM_SH="${NVM_DIR:-${HOME}/.nvm}/nvm.sh"
if [ -z "$INSTALLED_BIN" ] && [ -s "$NVM_SH" ]; then
    set +u
    # shellcheck source=/dev/null
    . "$NVM_SH"
    set -u
    INSTALLED_BIN="$(command -v gitnexus || true)"
fi
if [ -z "$INSTALLED_BIN" ]; then
    echo "gitnexus-pinned: gitnexus is not on PATH. Install it: npm i -g gitnexus@${PINNED}" >&2
    exit 127
fi

INSTALLED_VERSION="$("$INSTALLED_BIN" --version | tr -d '[:space:]' || true)"
if [ "$INSTALLED_VERSION" != "$PINNED" ]; then
    echo "gitnexus-pinned: ${INSTALLED_BIN} is ${INSTALLED_VERSION:-unknown}, pinned is ${PINNED}. Install the pinned version: npm i -g gitnexus@${PINNED}" >&2
    exit 127
fi

exec "$INSTALLED_BIN" "$@"
