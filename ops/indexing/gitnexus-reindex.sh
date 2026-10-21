#!/usr/bin/env bash
# Index ONE repository with the pinned GitNexus version, then replace the
# generated `.gitnexus/run.cjs` with the pinned runner.
#
# `gitnexus analyze` rewrites `.gitnexus/run.cjs` on every run, and the
# generated runner falls back to `gitnexus@latest`. Copying `gitnexus-run.cjs`
# over it keeps that route on the version in `gitnexus-version`.
#
# When the repository's `.gitnexusrc` sets `"embeddings": true`, `--embeddings`
# is added unless the caller already chose an embeddings option.
#
# This script commits nothing and pushes nothing.
#
# Usage: gitnexus-reindex.sh <repository path> [gitnexus analyze arguments...]
# Exit:  2 on a bad argument, else the status of `gitnexus analyze`.

set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: gitnexus-reindex.sh <repository path> [gitnexus analyze arguments...]" >&2
    exit 2
fi

REPO="${1%/}"
shift

if [ ! -d "${REPO}/.git" ] && [ ! -f "${REPO}/.git" ]; then
    echo "gitnexus-reindex: not a git repository: ${REPO}" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# An index carried along when a repository was moved or renamed names its old
# path, and GitNexus refuses to write over it ("storage path is in state
# foreign"). The index is ignored, regenerable data, so it is removed and
# rebuilt.
REPO_ABS="$(cd "$REPO" && pwd -P)"
META="${REPO_ABS}/.gitnexus/meta.json"
if [ -f "$META" ]; then
    INDEXED_PATH="$(sed -n 's/^  "repoPath": "\(.*\)",$/\1/p' "$META" | head -n 1)"
    if [ -n "$INDEXED_PATH" ] && [ -d "$INDEXED_PATH" ]; then
        INDEXED_PATH="$(cd "$INDEXED_PATH" && pwd -P)"
    fi
    if [ -n "$INDEXED_PATH" ] && [ "$INDEXED_PATH" != "$REPO_ABS" ]; then
        echo "gitnexus-reindex: the index in ${REPO_ABS}/.gitnexus was built for ${INDEXED_PATH}; removing it and indexing afresh" >&2
        rm -rf "${REPO_ABS}/.gitnexus"
    fi
fi

# A repository whose `.gitnexusrc` asks for embeddings gets `--embeddings` on
# every run, so new symbols are embedded whichever route starts the re-index.
EXTRA=()
if [ -f "${REPO}/.gitnexusrc" ] && grep -qE '"embeddings"[[:space:]]*:[[:space:]]*true' "${REPO}/.gitnexusrc"; then
    WANTS_EMBEDDINGS="yes"
    for arg in "$@"; do
        case "$arg" in
            --embeddings | --embeddings=* | --drop-embeddings) WANTS_EMBEDDINGS="no" ;;
        esac
    done
    if [ "$WANTS_EMBEDDINGS" = "yes" ]; then
        EXTRA+=(--embeddings)
    fi
fi

"${SCRIPT_DIR}/gitnexus-pinned.sh" analyze "$REPO" "$@" ${EXTRA[@]+"${EXTRA[@]}"}

cp "${SCRIPT_DIR}/gitnexus-run.cjs" "${REPO}/.gitnexus/run.cjs"
