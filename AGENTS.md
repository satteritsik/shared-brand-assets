<!-- BEGIN GITNEXUS: written by `polyrepo agents-sync` from repositories/agents-gitnexus-block.md in the SkillSpoke repo; edit it there -->
# GitNexus — Code Intelligence

This repository is indexed by GitNexus as **shared-brand-assets**. Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `"$SKILLSPOKE_CC/ops/indexing/gitnexus-reindex.sh" .` in the terminal from the repository root. Run every other GitNexus command through `"$SKILLSPOKE_CC/ops/indexing/gitnexus-pinned.sh" <command>`; never start GitNexus any other way, because the project pins one version in `ops/indexing/gitnexus-version` of the control repository.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/shared-brand-assets/context` | Codebase overview, check index freshness |
| `gitnexus://repo/shared-brand-assets/clusters` | All functional areas |
| `gitnexus://repo/shared-brand-assets/processes` | All execution flows |
| `gitnexus://repo/shared-brand-assets/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |
<!-- END GITNEXUS -->

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->

<!-- BEGIN SKILLSPOKE SHARED: written by `polyrepo agents-sync` from repositories/agents-shared-block.md in the SkillSpoke repo; edit it there -->
## SkillSpoke: instructions shared by every repository

This repository is one of the SkillSpoke project's repositories. The SkillSpoke
command-and-control repository (`$SKILLSPOKE_CC`) holds the instructions that apply across all
of them, in its `AGENTS.md` and `.claude/rules/`. A session here follows the same rules and
expectations as a session there; this block carries the ones that apply in every repository.

SkillSpoke is a personal job-search agent: it does the job search on the seeker's behalf, and
every capability serves the job seeker.

### Who works here

- **Understand before you act.** Analyze, plan and understand the problem, then do it right the
  first time. Never fix the same problem repeatedly by acting before understanding.
- The owner is the only human. Claude Code wrote all of the code and documentation, so anything
  found here, finished or not, is Claude Code's to own and fix. Never attribute it to anyone
  else, and do not use git history or blame to decide who did something.
- **Scope.** Fix what is broken inside the module you are working in, even if this session did
  not cause it. Report a problem outside it to the owner in one line with a proposed fix. Never
  stay silent about a problem.
- Only the owner runs `keeper.py` and `supervisor.py`. Never run, dry-run or rehearse them.

### Lifecycle and AWS

- SkillSpoke is in alpha testing: one development AWS account, at most five users expected.
  That is the context for cost, deployment-impact and capacity decisions, not a product limit.
- Do not engineer for hypothetical load. Weigh provisioned capacity with a monthly floor
  (ElastiCache node, idle RDS, NAT Gateway) against per-request pricing (DynamoDB on-demand,
  Lambda). If a decision needs a scale figure nobody has given, ask rather than invent one.
- Every `aws` and `cdk` command passes `--profile` right after the subcommand, with the value of
  the environment variable `SKILLSPOKE_AWS_PROFILE`: `--profile {{.SKILLSPOKE_AWS_PROFILE}}` in
  a Taskfile, `--profile "$SKILLSPOKE_AWS_PROFILE"` in a shell. The variable is required, with no
  default; never rely on the default profile or `AWS_PROFILE`. No code names, passes or reads an
  AWS account (no `-c account=`, no `CDK_DEFAULT_ACCOUNT`): the account comes only from the
  profile. The one fixed value is CodeArtifact, which always lives in account 616930583457.

### Where to find things

- **Project documentation** (product, architecture, research, glossary) lives in the
  `skillspoke-docs` Obsidian vault. Search it before answering what the product does, what was
  decided, or where a contract is defined. Use the `obsidian:obsidian-cli` skill and always pin
  the vault: `obsidian-cli vault="skillspoke-docs" search query="<terms>" limit=10`.
- Obsidian must be running. Empty output or "unable to find Obsidian" means the app is closed,
  not that nothing exists: ask the owner to open it.
- The architecture is the arc42 SAD under `docs/tech/architecture/arc42/` in the vault, kept
  with the `agent-teams-workforce:arc42` skills. Do not create ADRs.
- **Repository facts.** Ask the `polyrepo-steward` agent for anything about a repository other
  than its contents: which repo owns a function, where a repo is, whether it is up to date with
  GitHub, creating, renaming or deprecating one. For cross-repo code questions, search the
  GraphRAG MCP (`mcp__mcp-graphrag-server__search`) first.
- **Repository names.** `SkillSpoke-{name}` is the personal-agent app; `shared-{name}` is
  shared across the whole company; `marketing-{name}` is marketing; `employer-{name}` is the
  employer app. A repo is never deleted: it is deprecated by renaming it `deprecated-{name}`
  in lowercase, and archived on GitHub 60 days later.

### Code quality (this is the product: tier 1)

- Concurrency guards, timeouts, backoff, idempotency, incremental processing, explicit error
  handling and resource cleanup.
- Latest stable versions, no deprecated patterns, no clever solutions unless asked.
- Files under 500 lines. Typed interfaces for public APIs. Validate at system boundaries.
- Spec-first OpenAPI: the spec exists before handler code. TDD London School (mock-first) for
  new code.
- **No hardcoding.** Make behavior data-driven and configurable. Paths use the project's
  environment variables (for example `$SKILLSPOKE_ROOT`); if none fits, create one before
  writing a literal path. Never write a person's name into a rule, script, hook or config: say
  "the owner".
- **Errors stay visible.** `2>/dev/null` is not used in hooks, scripts or commands.

### Platform rules

- Lambda handlers use `aws-lambda-powertools`. FastAPI, Flask and Django are banned.
- API Gateway is REST API v1. HTTP API v2 is banned.
- **Service isolation.** A service never imports another service's code (no
  `from skill_spoke.common` or `from skill_spoke.services.<other>`) and owns its own DynamoDB
  tables; services do not share a table unless the owner explicitly says so for that scope.
- Cross-stack values go through SSM Parameter Store: the producing stack writes the parameter,
  the consuming stack reads it. Never CloudFormation exports or imports.
- **CDK.** Build every CDK app and stack with `shared-cdk-lib` (import `skillspoke_cdk`):
  `run_app`, `PlatformStack`, `Names`, `ChassisFunction`, `PlatformRestApi`, `PlatformTable`,
  `put_param`; never a copied `lambda_utils.py`. A repository not yet on it moves to it when a
  Task next changes its CDK.
- **Web UI.** Product UI is built with the `cds` plugin (the Configurable Design System), using
  its configuration and composition rather than a page-by-page design. Tables use AG Grid.
- **Issue tracking** is beads (`bd`), prefix `ssbd-`. Never run `bd init`.

### Git flow

- Work happens in a git worktree on a feature branch, never on `main` in the primary working
  tree. Never commit on `main` and never push to `main`.
- Commits: `type(scope): description`, no `Co-Authored-By`.
- Run `ruff check` on the files you changed before committing.
- `--no-verify` (and `git commit -n`) is forbidden. When a pre-commit hook fails, fix every
  finding, restage, and commit again. If a finding cannot be fixed, abort with no commit and
  report. Loosening lint config is the owner's decision.
- The agent that writes a change commits it, pushes the branch and opens the PR with
  `skillspoke-pr`, run inside the branch's worktree. Never `gh pr create`. An unpushed commit
  or a branch with no PR is unfinished work.
- `skillspoke-pr` arms auto-merge and chains the shepherd, which fixes until checks pass and
  threads resolve; GitHub auto-merge then merges. Never ask the owner to merge and never merge
  yourself: find what blocks auto-merge and clear it.

### Bash commands

Write each command so an unattended run never stops on a permission prompt:

- One command per call: no `&&` or `;` chains, no `for`, `while` or `if` on the command line.
- Absolute paths or `git -C <dir>`, never `cd <dir> && ...`.
- No command substitution (`$(...)`, backticks), `eval`, or paths built from shell variables.
- A destructive command (`rm`, `git worktree remove`, `mv` over files) names a literal path in
  a call of its own.
- Multi-step logic goes in a script file in the scratchpad; run the script.

### Name the thing

Use the real name of a system, tool, command, file, bead, repository or document, never a
generic category word (`ssbd-q5km`, not "the Epic"; `bd`, not "the tracker"). Take project
terms from the vault glossary (`docs/glossary/`, one note per term).
<!-- END SKILLSPOKE SHARED -->

<!-- BEGIN AGENT TEAMS WORKFORCE: written by `polyrepo agents-sync` from agents-file.md in the agent-teams-workforce plugin; edit it there -->
## Instructions for Agent Teams Workforce

This project uses Agent Teams Workforce (the `agent-teams-workforce` plugin): bounded
specialist agents that run the SDLC through workflow scripts, with maker, checker and approver
kept separate.

- **SDLC pipelines, workflow scripts, agent taxonomy, teams, full roster:**
  `AGENT-TEAMS-WORKFORCE.md` at the root of the installed plugin. The installed plugin root is
  the `installPath` of `agent-teams-workforce@mark-satterfield` in
  `$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json` (`$CLAUDE_CONFIG_DIR` defaults to
  `~/.claude`).
- **Commands:** the plugin's slash commands, `/agent-teams-workforce:<command>`.

# Agent Teams Workforce host instructions

The canonical orchestrator rules are imported below. They apply to the armed
orchestrator session in consuming projects, not to bounded subagents or the
source repository that builds this plugin. See the imported rules for legacy
project-wide arms and the exact boundaries.

@rules/CLAUDE.md
<!-- END AGENT TEAMS WORKFORCE -->
