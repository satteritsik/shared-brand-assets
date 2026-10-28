---
name: skillspoke-docs-vault
description: >-
  How to file and find documents in the `skillspoke-docs` Obsidian vault, SkillSpoke's documentation
  vault: where it is, the obsidian-cli commands pinned to it, its folder layout, the one home of
  each kind of fact, how its documents are classified, and the NotebookLM notebook that
  corroborates it. Load it before you search the vault for a fact, decide where a document
  belongs, or file, move or classify a document in it.
---

# The `skillspoke-docs` vault

The `skillspoke-docs` Obsidian vault is SkillSpoke's documentation and its source of truth. It is
its own git repository at `$SKILLSPOKE_ROOT/skillspoke-docs`, a symlink to a Google Drive shared
drive; its `docs/` folder is the same tree as `$SKILLSPOKE_SHARED_DOCS`. Content lives under
`docs/`; the working folders `copilot/`, `Inbox/` and `Tags/` sit at the vault root.

## Commands

Search and read the vault with `obsidian-cli` (the `obsidian:obsidian-cli` skill carries the full
command set), always pinning the vault as the first parameter:

```bash
obsidian-cli vault="skillspoke-docs" search query="<terms>" limit=10
obsidian-cli vault="skillspoke-docs" read path="docs/<path>"
obsidian-cli vault="skillspoke-docs" read file="<document name>"
obsidian-cli vault="skillspoke-docs" backlinks file="<document name>"
obsidian-cli vault="skillspoke-docs" properties counts
```

Obsidian must be running. Empty output or "unable to find Obsidian" means the app is closed, not
that nothing was found. Once a document is located, it may be read and edited directly on disk
under `$SKILLSPOKE_ROOT/skillspoke-docs/`, in Obsidian Flavored Markdown.

## Layout

| Folder | Holds |
|---|---|
| `docs/product/` | Product management and design: `product-design/` (`ui-design`, `ux-research`), `product-management/` |
| `docs/sdlc/` | SDLC documents: the document model (`sdlc.md`), BRDs, PRDs and specs by release (`mvp-1/`, `mvp-2/`), `sdlc-templates/` |
| `docs/tech/architecture/` | `arc42/` (the arc42 SAD), `detailed/` (`system/`, `cross-component/`, `components/<repository>/`, `features/<feature>/`), `built/`, `reference/` (the architecture documentation model and the diagram and model types) |
| `docs/tech/` | Other technical areas: `cloud-infrastructure`, `cybersecurity`, `data-engineering`, `infrastructure` (with `dns`), `internal-it`, `qa-testing`, `services`, `software-engineering` |
| `docs/revenue/` | `marketing`, `customer-support`, `revops` |
| `docs/general-admin/` | `legal-compliance`, `finance`, `hr`, `bizops` |
| `docs/glossary/` | Domain terms, one document per term |
| `docs/best-practices/` | Working documents and best-practice guidance |
| `copilot/`, `Inbox/` | AI prompts; the capture inbox (at the vault root, not under `docs/`) |

Document names carry drift: open or search before citing a path.

## Where each kind of fact goes

| The fact is about… | Files under |
|---|---|
| A domain term (one concept) | `docs/glossary/`, one document per term, holding the definition only |
| Architecture: a chosen technology, pattern, constraint or structure | the arc42 SAD, `docs/tech/architecture/arc42/`, edited with the `agent-teams-workforce:arc42` skills |
| Detailed architecture: names, types, keys, fields, routes or schemas, or what matters only to the people changing one component or feature | `docs/tech/architecture/detailed/`, linked from the arc42 view of its subject; `reference/architecture-documentation-model.md` states the test |
| Other technical matter | the matching `docs/tech/<area>/` folder |
| A product requirement, feature, positioning, UX research, brand character | `docs/product/` (ongoing product work) or `docs/sdlc/` (a PRD or spec deliverable) |
| An SDLC deliverable: BRD, PRD, spec, template, a release-scoped document | `docs/sdlc/` (for example `docs/sdlc/mvp-1/`) |
| Revenue: marketing, customer support, revops, brand and creative | `docs/revenue/` |
| Corporate and admin: legal, compliance, finance, HR, bizops | `docs/general-admin/` |
| A reusable way-of-working playbook or best practice | `docs/best-practices/` |
| A fact whose home cannot be decided | `Inbox/`, as a dated `capture-note` naming the candidate homes |

The SAD records current state. Section 9 (Architecture Decisions) is not used: SkillSpoke writes
no ADRs, and a decided fact goes into section 2, 4 or 8.

## Classification and corroboration

- Every vault document is classified with the `document-classification` skill (front matter
  `document_class`, `lifecycle_state`, `authority`, `purpose`, and the pointers that keep it
  honest). It is the authority on single source of truth and lifecycle states.
- For an architecture document (`arc42/` or `detailed/`), its lifecycle and iteration rules are
  in the `agent-teams-workforce:effective-architecture` skill, "Lifecycle, the baseline and
  iteration".
- The SkillSpoke NotebookLM notebook (through the `notebooklm` skill) and the GraphRAG index
  corroborate a vault search whose result is ambiguous. Nothing is authored there; NotebookLM is
  refreshed elsewhere.

## GitNexus graph index of the architecture and SDLC documents

GitNexus indexes `docs/tech/architecture/` and `docs/sdlc/`, and nothing else in the vault, as the
repository `skillspoke-docs`. It is in no GitNexus group.

- Query it with the GitNexus MCP tools and `repo: "skillspoke-docs"` (for example `query` for
  the documents about a concept, `cypher` over the `File` and `Section` nodes and their links).
- No agent re-indexes it. The elaboration pipeline re-indexes it once per architecture pass, after
  the documents are written, with a script step that runs `ops/indexing/gitnexus-docs-reindex.sh`
  of the command-and-control repository (`SkillSpoke`), with no arguments.
