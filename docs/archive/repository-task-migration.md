# Repository task migration

Taskman became the repository's delivery tracker on 2026-10-25. The operator accepted the
migration and closed its workstream. Ordinary-use feedback will be handled when encountered;
it is not an outstanding migration acceptance gate.

Current workflow and authorization rules are owned by [AGENTS.md](../../AGENTS.md) and the
[bundled CLI skill](../../priv/taskman_cli_skill/SKILL.md). This record preserves migration
provenance, source identity and verification, not a second task queue.

## Imported scope and identity

Four Lists received 14 Tasks: Product (1), Operations (6), Testing & CI (5), and Repository
maintenance (2). At import, six were Pending, seven Icebox and one In Progress. Source
Open/Deferred/In Progress mapped to Pending/Icebox/In Progress; P1/P2/P3 mapped to
High/Medium/Low. Current status belongs to Taskman.

Resolve the repository's current host and Project through `taskman repo show --json`. Each Task
ID below is relative to that registered Project and must match the exact
`Source: taskman/br/<source-id>` marker in its description. Missing or mismatched markers are
unresolved references; do not guess another Project or revive Beads writes.

| Historical source | Task ID relative to the registered Project |
| --- | ---: |
| `tas-e2tm` | 31 |
| `tas-bbry` | 32 |
| `tas-7ncz` | 33 |
| `tas-sidn` | 34 |
| `tas-hmrr` | 35 |
| `tas-jady` | 36 |
| `tas-lny6.4.7` | 37 |
| `tas-n9pb` | 38 |
| `tas-4m2w` | 39 |
| `tas-i5t` | 40 |
| `tas-29kw` | 41 |
| `tas-upgrade-alpine-ci-fixed-otp-mt1` | 42 |
| `tas-y0l8` | 43 |
| `tas-lx0f` | 44 |

Full descriptions, acceptance/design fields, notes, external references and historical comments
with attribution/timestamps were preserved. Relationships to closed, excluded work remain
historical references rather than active blockers. Completed source work remains in the private
archive. There is no ongoing synchronization or second writable queue.

Excluded source records were `tas-en2g` (delivered hosted-access fix, PR 27 at `f882deb7`),
`tas-b7kd` (superseded provisioning/consolidation), and `tas-cs2i` (transitional archive repair).
Their source records were not closed or deleted by the import.

## Private source custody

The entire `.beads/` directory is untracked and ignored. Its current local export, database,
configuration, historical records and recovery/quarantine material were preserved; all 409 files
were verified byte-identical across untracking. Local configuration resolves `actor: operator`.
The authorized source-path migration removed `source_repo_path` from all 355 exported records
without changing other record content. Source maintenance requires explicit operator scope.

The import-time 355-record snapshot had SHA-256
`c109cf285c4a3ceafaffd3ea067428adaddfcbbd4be7682ad50a86cb6987e0d1`.
Protected source snapshots, the import plan, mapping and verification receipts remain in private
operator storage under custody name `migrations/taskman-br-2026-10-25`. The hash predates the
source-path migration. Do not publish raw receipts or delete retained recovery material.

Task 43 retains publication/history privacy work; untracking current files does not sanitize
earlier history. Task 35 retains the hosted service's off-host-backup work. Migration completion
does not complete either follow-up or establish native deployment, backup or recovery acceptance.

## Verification

All 14 full imported records and four List locations were compared against the private plan.
A subsequent live comparison confirmed source status, priority, content, notes, historical comments
and relationship references. The primary clone and its two linked worktrees were checked against
the registered Project; independent clones require their own registration.

Focused onboarding/bundle/installer tests passed (27), rebuilt executable tests passed (3), and
skill validation plus `skillset doctor` passed. Independent review found the generic workflow
contracts preserved. Full `mix precommit` passed on 2026-10-25: 1274 passed, 3 skipped and
32 browser tests excluded, seed 119784, in 55.2 seconds. No native deployment or recovery
acceptance was performed for the migration.

The shared workflow skill changes landed in Skillsets `main` at
`db3caf227f4d52034e457e82ef723ac060e57ac9`. Orchestration's supporting migration record is
`docs/research/taskman-task-tracking-migration.md` in that repository at `f5a68cc2`; the separate
Qwazar migration and overlap review retain their own scope.
