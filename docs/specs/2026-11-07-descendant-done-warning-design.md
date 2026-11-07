# Descendant checks before marking a Task Done

Status: implemented, locally verified and operator accepted.

Extend the existing Done warning to unfinished descendants. A descendant that resolves a Done
blocker forms a boundary: its own subtree has already been accepted and must not warn again
when an ancestor is marked Done.

## Decisions

| ID | Status | Choice | Cost or constraint | Rationale |
| --- | --- | --- | --- | --- |
| D1 | Accepted | Inspect all descendant paths, stopping at each resolving node | Unfinished work beneath a resolving node is deliberately hidden from this warning | [Traversal](#descendant-traversal) |
| D2 | Accepted | Use the existing `resolves_blocker?` classification | Behavior follows future classification changes rather than a fixed status name | [Classification](#classification-and-existing-contracts) |
| D3 | Accepted | Extend the existing combined warning and confirmation interfaces | Existing force confirmation also overrides descendant warnings | [Integration](#integration-and-approaches) |
| D4 | Accepted | Keep query count independent of descendant count and depth; no reads per child or subtree | Constant query count does not make database work or result size constant | [Query efficiency](#query-count-must-not-grow-with-the-tree) |

All four design decisions and the delivered behavior are operator accepted.

## Descendant traversal

Start from the current persisted Task's immediate children. For every visited descendant:

1. Evaluate `Taskman.Tasks.Status.resolves_blocker?(descendant.status)`.
2. If true, exclude that descendant and stop traversing that branch.
3. If false, include that descendant in the warning and continue through its children.

Exclude the Task being marked Done from this traversal. Read actual parentage within its Project;
List membership, collapsed branches and display filters do not constrain the check. Do not follow
Blocks edges while traversing descendants. Existing unresolved direct Blocks prerequisites are
checked independently, including a direct blocker otherwise hidden beneath a resolving descendant.

Use current classification only: do not inspect prior confirmation history. A previously resolving
node that becomes non-resolving exposes its descendants to the ancestor's next Done attempt.
New children and reparenting do not automatically reopen any Task.

The following examples use today's classifications: Done resolves; Pending does not. Arrows mean
parent to child, starting below the Task being marked Done.

| Descendant branch | Descendants requiring confirmation |
| --- | --- |
| Pending child → Pending grandchild | Both |
| Done child → Pending grandchild | Neither |
| Pending child → Done grandchild → Pending great-grandchild | Only the child |
| Not Planned child → Will Not Do grandchild | Both |
| Done child → Pending grandchild that directly blocks the selected Task | Grandchild, through the independent direct-blocker check |

## Classification and existing contracts

`lib/taskman/tasks/status.ex` owns `resolves_blocker?` and exposes `Status.resolves_blocker?/1`.
Use that predicate for in-memory checks. Database traversal may use a resolving-status set derived
from these same definitions; do not hardcode `:done`, the string `"done"`, or an independent list
of resolving states in descendant filtering or recursion boundaries.

The trigger remains the existing explicit transition into Done. The operator's classification
requirement applies to deciding whether related Tasks resolve blockers, not to renaming or expanding
the existing target transition. Today Not Planned and Will Not Do do not resolve blockers.

Inherit the current [relationship lifecycle contract](../product/relationships.md#lifecycle-behavior)
and [Done confirmation design](2026-09-27-task-blocking-relationships-design.md#done-update-and-confirmation):

- Warn on an existing Task's transition from a non-Done state into Done, leaving the whole update
  unpersisted. Retain optimistic conflict and validation checks.
- An already Done Task's ordinary edit or status no-op needs no new confirmation. Do not reopen
  Tasks automatically or create implicit Blocks records for children.
- Explicit confirmation can override a warning. A newly observed unconfirmed Task ID requires a
  fresh warning; an unchanged, smaller or empty set remains confirmed.
- Browser departure discards the warned Done draft while retaining ordinary pending edits under
  the existing autosave rules. Resolving descendants must not turn departure into implicit consent.

This design supersedes the direct-blocker-only warning scope in the
[relationship lifecycle contract](../product/relationships.md#lifecycle-behavior).

## Integration and approaches

**Selected: one combined review set.** Union unresolved direct blocker summaries with the
descendant traversal result and deduplicate by Task ID. Preserve the current stable ordering by
case-insensitive Project name, title and ID. A Task can qualify through both relationships; confirming
its ID acknowledges either reason. Render one warning, headed “Review unresolved blockers and
descendants”, with the existing summary fields and Keep current status / Mark Done anyway actions.
Do not add a second confirmation step or require users to acknowledge relationships separately.

Keep the domain error `{:unresolved_blockers, summaries}` and the existing confirmation option
`done_confirmation: {:ids, ids}` or `done_confirmation: :force`. Recompute the union under the
existing graph lock immediately before allowing the write. All status and parentage mutations
affecting that union must continue participating in the same locking contract.

The authenticated PATCH route remains `/api/v1/projects/:project_id/tasks/:task_id`. Keep HTTP 409,
`error.code = "unresolved_blockers"`, and `error.blockers` containing the existing linked Task
summary shape. Change its message to “Review unresolved blockers and descendants before marking
Done”. No additional response fields are required. Keep the request's mutually exclusive
`confirmation.unresolved_blocker_ids` and `confirmation.force_done_with_unresolved_blockers = true`.
Confirmed IDs now cover the combined review set; force covers both sources for that one request.
Existing malformed-confirmation errors remain HTTP 400 `invalid_request`.

Keep CLI `--confirm-unresolved-blockers ID,ID` and `--force-done-with-unresolved-blockers`, HTTP 409's
existing CLI exit status 3, and the current structured error output. Update help, Bash/Fish
completion descriptions and the bundled CLI skill to explain the expanded meaning. Existing clients
that supply IDs only for direct blockers receive a fresh warning when uncovered descendants exist.
The force option gains broader scope; retain it as an explicit one-request completion override.

**Alternative: separate descendant confirmation.** Separate error fields and confirmation options
make the reason explicit and preserve a narrower interpretation of blocker force, but require new
client contracts and coordination when both kinds of warning apply. The combined set reuses the
existing reviewed-task acknowledgement model and avoids duplicate prompts. No API renaming or
compatibility aliases are proposed.

## Query count must not grow with the tree

Do not issue separate queries per child, level or subtree. The operator accepts either bulk loading
the relevant hierarchy for an in-memory walk or a constant number of database queries. Query count
must remain independent of both tree width and depth; runtime and returned data can still grow.

The implementation uses one recursive CTE statement, with classification-based pruning
in both its seed and recursive member. PostgreSQL performs the recursion within that statement;
there are no application-driven queries for each recursive step. This transfers only eligible IDs.
Bulk loading the relevant hierarchy and walking it in memory is also valid, but transfers nodes
below resolving boundaries that the CTE can prune. Avoid loading unrelated Project Tasks.

Warning collection uses at most four data reads: one descendant traversal, one incoming
Blocks read, and the existing two bulk summary/location reads in `BlockingPersistence.summaries/1`.
Existing graph locking and Task reloads are separate fixed overhead. Any implementation change
must preserve a constant query count across the whole collection, including location summaries.

## Implementation boundaries and evidence

The implementation keeps the existing persistence and confirmation boundaries:

- `lib/taskman/tasks/hierarchy.ex`: `unresolved_descendant_ids/1` selects eligible IDs with one
  Project-scoped recursive CTE. Both seed and recursion derive non-resolving statuses from
  `Status.values/0` and `Status.resolves_blocker?/1`, pruning before recursion.
- `lib/taskman/tasks/mutations.ex`: union and deduplicate direct-blocker and descendant IDs before
  bulk summary loading; retain atomic graph-lock confirmation, stable ordering and conflict checks.
- `lib/taskman_web/live/project_live/tasks/autosave.ex` and
  `lib/taskman_web/components/tasks/detail.ex`: preserve domain summary order and the existing
  draft/confirmation lifecycle, with the combined warning heading.
- API fallback and CLI registry/help retain existing summary fields, error code, status and options.
  The bundled CLI skill explains branch boundaries, combined ID review and the expanded force scope.
- [Relationships](../product/relationships.md#lifecycle-behavior),
  [domain](../product/domain.md#task-relationships) and
  [MVP behavior](../product/mvp-spec.md) describe the delivered rule.

No schema migration, additional relationship type, lifecycle automation or deployment work is needed.
Database work and result size can still grow with eligible descendants; constant query count is not
a runtime or memory bound.

## Verification and approval scope

Local automation verifies the traversal scenarios, every supported resolving classification,
cross-List hierarchy, independent direct blockers, deduplication, combined ID/force confirmation,
diminishing sets, reopened boundaries and parentage changes. Mixed updates remain atomic; already-Done
edits, validation and optimistic conflicts retain their existing rules. Queued graph-lock tests prove
that status and parentage changes refresh the review set before confirmation writes.

LiveView coverage exercises combined ordering, cancellation, confirmation, refreshed warnings and
departure without implicit consent. API coverage verifies the exact 409 message and linked summary
shape, refusal of incomplete combined IDs, successful combined IDs and force, and unchanged malformed
confirmation errors. Req.Test CLI coverage preserves JSON summaries, exit 3 and both request payloads;
generated help explains descendants and real Bash/Fish completion queries still offer both flags.

Telemetry verifies exactly one SQL statement for each descendant-helper call and equal total query
counts for the full warning as a nonempty tree grows wider and deeper. Returned IDs are asserted:
one eligible descendant in the small tree and four in the larger tree, excluding a resolving branch
and its deeper unfinished nodes. Fixtures are created outside capture windows. This establishes
query-count behavior, not a wall-clock performance benchmark.

Local verification on 2026-11-07 used Elixir 1.20.4 and Erlang/OTP 29.1.2:

| Gate | Result |
| --- | --- |
| `mix test test/taskman_web/controllers/api/task_controller_test.exs` | 49 passed |
| `mix test test/taskman/cli` | 302 passed; 3 explicit executable tests skipped |
| `mix precommit` | 1,554 passed; 3 explicit executable tests skipped; 81 browser tests excluded |
| `mix escript.build` | Rebuilt executable generated successfully |
| `TASKMAN_TEST_ESCRIPT="$PWD/taskman" mix test test/taskman/cli/escript_test.exs` | All 3 executable smoke tests passed; none skipped |

CLI gates ran sequentially against disposable configuration and Git roots; the generated executable
remains ignored. No browser visual inspection, live-backend CLI acceptance or pinned release-runtime
verification is claimed. Operator acceptance remains separate from these automated checks.

Design approval covers the shared confirmation contract, including the expanded force scope.
The delivered behavior is operator accepted. Publication and deployment retain separate
authorization.
