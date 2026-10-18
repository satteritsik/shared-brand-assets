---
name: gitnexus-cli
description: "Use when the user needs to run GitNexus CLI commands like analyze/index a repo, check status, clean the index, generate a wiki, or list indexed repos. Examples: \"Index this repo\", \"Reanalyze the codebase\", \"Generate a wiki\""
---

# GitNexus CLI Commands

Every command runs through the pinned wrapper `"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh"`, which runs the one GitNexus version the project pins (`ops/indexing/gitnexus-version` in the control repository). Never start GitNexus any other way: an unpinned run can write an index the other readers cannot use.

## Commands

### analyze — Build or refresh the index

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-reindex.sh" .
```

Run from the project root. `gitnexus-reindex.sh` parses all source files, builds the knowledge graph and writes it to `.gitnexus/`; it commits nothing. Extra arguments after the path go to `analyze` (the flags below).

| Flag           | Effect                                                           |
| -------------- | ---------------------------------------------------------------- |
| `--force`      | Force full re-index even if up to date                           |
| `--embeddings` | Enable embedding generation for semantic search (off by default) |
| `--drop-embeddings` | Drop existing embeddings on rebuild. By default, an `analyze` without `--embeddings` preserves them. |

**When to run:** Rarely by hand. Every SkillSpoke repository re-indexes itself: a git config hook (`skillspoke-gitnexus`, registered by `polyrepo gitnexus-sync`) queues a background re-index after every commit, merge, checkout and rebase, and the SessionStart hook queues every repository whose index is behind its current commit. Run it by hand when a session-start warning names a failed re-index (after fixing the cause), or when `gitnexus://repo/{name}/context` reports the index is stale and no re-index is queued.

### Fleet status — is every repository indexed?

```bash
python3 "$SKILLSPOKE_CC/ops/indexing/gitnexus-fleet.py" status
```

Checks that the GitNexus MCP server answers at the pinned version, that the `skillspoke` group exists, and that every member repository is set up and indexed at its current commit. Prints each problem with the command that fixes it and exits 1 when there is one. Re-index logs and outcomes are in `$SKILLSPOKE_LOGS/gitnexus/`. The same checks run at every session start and are shown to the owner when anything is wrong.

### status — Check index freshness

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" status
```

Shows whether the current repo has a GitNexus index, when it was last updated, and symbol/relationship counts. Use this to check if re-indexing is needed.

### clean — Delete the index

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" clean
```

Deletes the `.gitnexus/` directory and unregisters the repo from the global registry. Use before re-indexing if the index is corrupt or after removing GitNexus from a project.

| Flag      | Effect                                            |
| --------- | ------------------------------------------------- |
| `--force` | Skip confirmation prompt                          |
| `--all`   | Clean all indexed repos, not just the current one |

### wiki — Generate documentation from the graph

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" wiki
```

Generates repository documentation from the knowledge graph using an LLM. Requires an API key (saved to `~/.gitnexus/config.json` on first use).

| Flag                | Effect                                    |
| ------------------- | ----------------------------------------- |
| `--force`           | Force full regeneration                   |
| `--model <model>`   | LLM model (default: minimax/minimax-m2.5) |
| `--base-url <url>`  | LLM API base URL                          |
| `--api-key <key>`   | LLM API key                               |
| `--concurrency <n>` | Parallel LLM calls (default: 3)           |
| `--gist`            | Publish wiki as a public GitHub Gist      |

### list — Show all indexed repos

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" list
```

Lists all repositories registered in `~/.gitnexus/registry.json`. The MCP `list_repos` tool provides the same information.

### group — Work across repositories

Every SkillSpoke repository is a member of the GitNexus group `skillspoke` (the name is in `ops/indexing/gitnexus-group` of the control repository). A member's group path is its clone's path under `$SKILLSPOKE_APPS`, for example `personal-agent/SkillSpoke-chat-service` or `shared/common/shared-auth-lib`. `polyrepo gitnexus-sync` keeps the membership equal to the steward's repositories; never add or remove members by hand.

```bash
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" group list skillspoke              # members
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" group status skillspoke            # staleness of the group and its repos
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" group query skillspoke "<concept>"  # execution flows across every member
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" group impact skillspoke --help     # cross-repository blast radius of a symbol
"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" group sync skillspoke              # rebuild the cross-repository contracts
```

The background re-index runs `group sync` after it re-indexes anything. Through MCP, pass `repo: "@skillspoke/<group path>"` to `query` or `impact` to work across repositories from that member; always name the member, because `@skillspoke` alone starts from the first member in the group.

## After Indexing

1. **Read `gitnexus://repo/{name}/context`** to verify the index loaded
2. Use the other GitNexus skills (`exploring`, `debugging`, `impact-analysis`, `refactoring`) for your task

## Troubleshooting

- **"Not inside a git repository"**: Run from a directory inside a git repo
- **Index is stale after re-analyzing**: Restart Claude Code to reload the MCP server
- **Embeddings slow**: Omit `--embeddings` (it's off by default) or set `OPENAI_API_KEY` for faster API-based embedding
