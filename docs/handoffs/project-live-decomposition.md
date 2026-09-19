# ProjectLive decomposition

- Status: completion acknowledged; ruling acknowledgement pending
- Updated: 2026-09-19
- Resume: `$resume project-live-decomposition`

## Objective

Split `TaskmanWeb.ProjectLive` into explicit workflow modules without changing its routes, socket
ownership, streams, events, or user-visible behavior.

## Durable references

- Design and implemented contracts: [ProjectLive workflow decomposition](../specs/2026-09-03-project-live-decomposition-design.md)
- Retired plan: [ProjectLive workflow decomposition implementation](../archive/plans/2026-09-04-project-live-decomposition.md)
- Delivery tracking: `tas-1tq`; completed extraction tasks `tas-1tq.1` through `.9`
- Delivery PR: [#17](https://github.com/Incanus3/taskman/pull/17)

## Current checkpoint

The operator explicitly confirmed the decomposition workstream complete. All nine extraction tasks
are implemented and independently reviewed. ProjectLive is the sole workspace LiveView and a
182-line coordinator. The verified baseline passed 264 focused tests, structural and formatting
checks, 829 full `mix precommit` tests, and PR CI without compiler warnings.

Missing List recovery is a separate parked workstream with its own handoff and deferred Beads owner.
It will resume later on a dedicated branch from refreshed `main`.

The decomposition handoff was retired after completion and durable harvesting but before the
operator was shown the autonomous implementation rulings for explicit acknowledgement. It has been
restored to preserve that gate. No implementation work remains.

## Remaining execution sequence

1. Current position: present and discuss every pending ruling below. Record the operator's explicit
   acknowledgement, revision, or rejection for each one.
2. Apply any requested corrections and update the canonical design if an accepted contract changes.
3. After every ruling is explicitly resolved, retire this handoff and update the handoff index.
4. Amend the documentation commit and refresh PR #17. Merge remains separately authorized.

## Pending decision

The operator must explicitly acknowledge, revise, or reject the pending autonomous rulings below.
Workstream completion has already been acknowledged and does not imply their acceptance.

## Workstream rulings

The original extraction ledger is restored below. Every entry remains pending explicit operator
acknowledgement, including decisions later superseded or corrected; their status describes the
implemented outcome rather than operator acceptance.

1. **Pending acknowledgement; current — review scope:** Review this session's extraction and
   relevant interactions rather than unrelated pre-existing branch changes, as required by
   repository guidance. Cost if wrong: unrelated branch defects remain outside this review.
2. **Pending acknowledgement; superseded by 4 — opening autosave:** Initially interpreted the
   design's persisted baseline as a saved opening status and the plan's “unsaved” wording as a typo.
   Cost if wrong: opening behavior and tests would change; baseline inspection corrected this
   interpretation.
3. **Pending acknowledgement; implemented — stale fixture migration:** Move the autosave
   parent-conflict test's `selected_project` injection to `workspace.selected_project`; the stale
   fixture failed at the extraction baseline and grouped state requires the new path. Cost if wrong:
   fixture migration could mask a contract mismatch; focused autosave tests and independent review
   covered it.
4. **Pending acknowledgement; implemented; replaces 2 — pure editing initialization:** Preserve
   `Autosave.load(saved?: false)`, idle status and sequence. `Editing.State.open/4` receives prior
   state, Task, prebuilt Autosave and hierarchy; orchestration constructs the context-dependent
   form. Tests use `baseline.id`. Cost if wrong: the internal constructor differs from the
   illustrative plan; baseline behavior, purity and direct tests govern.
5. **Pending acknowledgement; superseded by 6 — creation cleanup:** Initially allowed omission of
   creation clearing on missing hierarchy only if all reachable callers already had empty creation
   state, to avoid a workflow dependency. Cost if wrong: a stale creation draft survives; review
   found a reachable late-validation counterexample.
6. **Pending acknowledgement; implemented; replaces 5 — missing-detail cleanup:** Permit the
   minimal acyclic `Editing -> Creation.clear/1` dependency to preserve existing all-modal cleanup.
   Creation does not depend on Editing; a direct owner call avoids duplicate state mutation or a
   larger protocol. Cost if wrong: one additional explicit dependency; RED/GREEN regression and
   re-review covered it.
7. **Pending acknowledgement; implemented — hierarchy constructor contract:** `State.open/4`
   accepts domain `Taskman.Tasks.Hierarchy`; pure UI `Hierarchy.load` occurs inside and preserves
   disclosure state. Remove prewrapped hierarchy from the plan example. Cost if wrong:
   documentation/constructor types mismatch production; direct tests and re-review covered the
   contract.
8. **Pending acknowledgement; implemented — parent conflict refresh:** Preserve conflict-resolution
   sync-only behavior separately from normal parent-save sync/hierarchy/listing refresh, matching
   removed baseline code. Cost if wrong: an original incidental refresh limitation remains;
   reviewer reconsidered and approved the exact preservation boundary.
9. **Pending acknowledgement; implemented — post-move editing refresh:** Use dedicated
   `Editing.refresh_after_move/2` with scoped refetch, selected-ID guard,
   `Autosave.load(saved?: true)` and retained sequence; generic persisted-Task synchronization
   changes the saved indicator. Cost if wrong: one extra internal lifecycle API; movement
   interactions and saved-status tests covered it.
10. **Pending acknowledgement; implemented — missing-location outcome:** Return
    `{:location_missing, task_lists}` with already loaded Lists, so Creation canonicalizes a
    parent-derived surviving List before Listing clears. Avoid a duplicate query or
    `Workspace -> Creation` dependency. Cost if wrong: the outcome differs from the plan example;
    contract/reconciliation tests and review covered it.
11. **Pending acknowledgement; implemented, synchronization condition corrected by 12 — notification
    order/API:** Preserve Listing -> Movement -> Editing -> ParentSelection -> conditional hierarchy
    order from the baseline. Pass the persisted selected Task to `ParentSelection.sync/2` rather
    than an event requiring another refetch. Cost if wrong: illustrative plan order/API differs;
    final review discovered that retained selected state alone does not prove successful lookup.
12. **Pending acknowledgement; implemented; corrects 11's unconditional synchronization — explicit
    lookup outcome:** `Editing.reconcile/2` returns
    `{socket, {:task_reconciled, persisted_task}}` on lookup success and `{socket, :unchanged}`
    otherwise. Remove only its external picker reconciliation; the coordinator calls
    ParentSelection once only after success. Preserve scheduled-autosave picker behavior and
    original order. Cost if wrong: an explicit internal outcome API and direct tests require
    migration; missing-Task/query-multiplicity regressions and scoped re-review passed.
13. **Pending operator acknowledgement — publication branch:** the reviewed work was published as
   `project-live-decomposition-implementation` because the historical remote
   `project-live-decomposition` branch was unrelated and reusing it required a force push. Cost if
   wrong: the chosen name is less concise; overwriting the historical branch would instead risk
   destroying unrelated remote history.
14. **Operator rejected; superseded — retirement immediately after completion and harvesting:** the
    handoff was retired once completion was confirmed and lasting material was harvested. The
    operator rejected that as premature because autonomous rulings had not been explicitly
    acknowledged. The handoff is restored; the replacement rule requires ruling acknowledgement
    and pre-merge retirement. Cost realized: the ruling discussion was temporarily hidden and the
    workstream appeared fully closed.
