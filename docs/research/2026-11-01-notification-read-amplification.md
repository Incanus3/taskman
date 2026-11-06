# Opportunities to reduce notification-driven reads

**Date:** 2026-11-01
**Evidence:** Static inspection of current notification producers and LiveView consumers.
No performance benchmark or implementation verification has been performed.

Follow-up: Task **70**, relative to this repository's registered Taskman Project.
Validate its description marker: `Source: Incanus3/taskman:notification-read-amplification`.

The [additional-state design](../specs/2026-11-01-additional-task-states-design.md)
already calls for persisted Task `status` and `list_id` in events, eliminating extra availability
reads. The candidates below are findings for scope discussion, not additions to that feature.

## Small payload candidates

| Consumer | Additional data | Potential avoided reads | Constraint |
|---|---|---|---|
| Comment activity timestamp | The timestamp written to the owning Task | The Task fetch used specifically to synchronize its activity time | Apply timestamps monotonically; comment retrieval still validates ownership/existence separately |
| Already-loaded hierarchy node | A complete small presentation snapshot: title, status, priority, existing identity/revision and ownership metadata | Whole-hierarchy reload for scalar changes to an existing node | Revision handling is required; structural changes still require authoritative reload |
| Project/List navigation presentation | Persisted name/icon/color for Projects; name and parent identity for Lists | Refetching whole Project/List collections for presentation-only updates | Project/List records have no optimistic revision; timestamps alone must not be assumed to totally order concurrent updates |

Comment timestamps are already known to the publisher: comment creation writes `comment.created_at`
to the Task's `updated_at`. The receiver currently fetches the Task to copy that timestamp, then
loads comments through a context which also checks the Task. Adding the exact timestamp can remove
the timestamp-specific fetch without pretending to remove all comment-path reads. The local
post-success path also refetches the Task for its timestamp.

Publishing the rendered comment itself could additionally avoid reloading the thread and resolving
authors on every new comment, but text may be up to 10,000 graphemes. This is a larger payload and
requires deduplication, chronological insertion despite out-of-order delivery, current author
presentation semantics, and resnapshot behavior. It is not equivalent to adding one timestamp.

Hierarchy source data includes the entire connected tree. A title/status/priority update to an
already-loaded node can potentially patch that tree and recompute the existing in-memory projection
and sort. The present path obtains the hierarchy through a transaction, Project share lock, Task
read, ancestor query, connected-descendant query and List loading. Avoiding that reload is therefore
more significant than eliminating one small lookup. Parentage, creation, membership changes and
unknown/gapped authority still need deliberate reload rules. Do not drop an older structural
invalidation merely because a newer scalar snapshot was applied.

## First avoid refreshes unrelated to the changed data

Some opportunities require no new event fields:

- `Reconciliation.reconcile_task_event/2` refreshes the listing and counts for every valid matching
  Task event. Description-only and due-date-only changes do not affect current table content,
  filtering or sorting. Title/priority changes do not change location/status population counts.
  Dispatching by the existing changed-field metadata can avoid unrelated work.
- `Editing.reconcile/2` ignores its event argument and refetches the selected Task whenever its
  caller handles a Project Task event, even when the event identifies a different Task. Gate the
  selected Task's scalar reconciliation by identity, preserving independent related-task and
  hierarchy invalidations.

These are bounded candidates, not proof that every downstream workflow can be skipped wholesale.
Movement, recovery, parent pickers and hierarchy consumers have independent dependencies.

## Counts and full listing deltas are a larger change

Old/new owning location and state could support count deltas. Exact counts require event
deduplication, ordering or version handling, snapshot boundaries and recovery from missed events;
unlike observed-state sets, they cannot safely apply every notification by addition alone.
Filtered/hierarchical rows also depend on membership, ancestors, descendants and ordering.
Adding two scalar fields does not eliminate those queries by itself.

Prioritize field/identity-based refresh gating and the timestamp-specific comment read before a
general delta-driven UI. Evaluate scalar hierarchy snapshots separately with focused correctness
and query-count evidence. Broader Project/List snapshot handling needs a freshness design first.

## Source boundaries

- [Notification events and publication](../../lib/taskman/change_notifications.ex)
- [External-event coordinator](../../lib/taskman_web/live/project_live/reconciliation.ex)
- [Listing and count refresh](../../lib/taskman_web/live/project_live/tasks/listing.ex)
- [Task detail and hierarchy reconciliation](../../lib/taskman_web/live/project_live/tasks/editing.ex)
- [Hierarchy database loading](../../lib/taskman/tasks/hierarchy.ex)
- [Comment creation](../../lib/taskman/tasks/comments.ex), [timestamp persistence](../../lib/taskman/tasks/comment_persistence.ex), and [comment UI](../../lib/taskman_web/live/project_live/tasks/comments.ex)
- [Workspace reconciliation](../../lib/taskman_web/live/project_live/workspace.ex)
- [Existing invalidation/refetch contract](../specs/2026-09-01-live-task-updates-design.md)
