# Taskman — Task Relationship Semantics

**Status:** Resolved product decision
**Decided:** 2026-07-14; MVP scope revised 2026-09-27

## Vocabulary and records

The MVP has two explicit Task relationship types. They are independent of a Task's owning
location: every Task remains directly owned by its Project or one List, even when it has a parent
or relationships across Projects.

| Type | Meaning | Scope | Cardinality and invariants |
| --- | --- | --- | --- |
| **Blocks / Blocked by** | A directed prerequisite. If A blocks B, B is blocked by A. | May cross Projects. | One edge per ordered Task pair; no self-edge and no cycle, including a cross-Project cycle. |
| **parent-child** | A work-breakdown relationship. | Same Project only. | A parent has many children; a child has at most one parent; the hierarchy is acyclic. |

Parent-child and Blocks may coexist for the same Task pair. A parent may **never** block its own
child. A child may block its parent if the global Blocks graph remains acyclic.

Symmetric **Relates to** links were previously part of the MVP contract and are now deferred beyond
the MVP. They provide contextual navigation but no prerequisite or completion behavior. Their
eventual value and exact contract should be decided from use after blocking links are delivered;
the MVP does not require their storage, UI, API, CLI, or deletion handling.

## Lifecycle behavior

Task relationships never automatically alter Task state.

- Every lifecycle transition remains available to a human.
- An unconfirmed move from a non-Done state to **Done** warns about one combined set of unresolved
  direct blockers and descendants. The entire update remains unpersisted until explicitly confirmed.
- Descendant traversal starts at immediate children within the Task's Project and crosses Lists,
  collapsed branches and display filters. Each non-resolving descendant is included, then traversal
  continues through its children. A resolving descendant and its entire subtree are excluded.
  Direct Blocks prerequisites are checked independently, even below a resolving descendant.
- Only **Done** currently resolves blockers. **Not Planned** and **Will Not Do** are closed for
  counting but remain non-resolving, so descendant traversal continues through them. Current
  classification governs each attempt: reopening a resolving descendant exposes its branch again.
- The combined set is deduplicated by Task ID and ordered by case-insensitive Project name, title
  and ID. Confirming a Task ID acknowledges either relationship reason.
- API and CLI may confirm known combined IDs on the first request or use a case-specific,
  one-request force override covering both sources. ID confirmation remains valid if the combined
  set is unchanged, smaller or empty; a newly observed unconfirmed ID requires a fresh warning.
- Children never create implicit Blocks records. New children, reparenting and relationship edits
  do not automatically reopen Tasks or otherwise change their lifecycle state.
- A Task that remains Done needs no new warning for an unchanged status or an ordinary field edit.
  Explicit status requests retain optimistic conflict checks: a stale Done request cannot silently
  succeed after another user reopens the Task. A warning or conflict rejects all fields in the
  same update.

The API returns HTTP 409 with `error.code = "unresolved_blockers"`, message
“Review unresolved blockers and descendants before marking Done”, and the existing linked Task
summaries in `error.blockers`. The existing mutually exclusive confirmation fields remain
`confirmation.unresolved_blocker_ids` and `confirmation.force_done_with_unresolved_blockers = true`.
Malformed confirmation remains HTTP 400 `invalid_request`. CLI warnings retain exit 3 and the
`--confirm-unresolved-blockers ID,ID` / `--force-done-with-unresolved-blockers` options.

The browser presents one “Review unresolved blockers and descendants” warning with Keep current
status / Mark Done anyway actions. Leaving discards the warned Done draft while ordinary pending
edits follow existing autosave rules; resolving descendants cannot make departure implicit consent.
See the [descendant Done warning design](../specs/2026-11-07-descendant-done-warning-design.md)
for traversal, atomic confirmation and query-efficiency constraints.

## Creation and display rules

- Relationship creation rejects invalid scope, self-links, duplicate edges, forbidden parent-to-child
  dependency direction, and a resulting Blocks or parent-child cycle before persisting anything.
- A directed Blocks record is presented from either endpoint as **Blocks** or **Blocked by**.
- The Task detail hierarchy displays only parent-child. Its Related Tasks table displays Blocks /
  Blocked by. Cross-Project entries identify their other Project.

## Relationship Activity

Actual parent and directed blocking changes record one canonical event, attributed to the
account performing the mutation. The mutation and its event/Task associations commit together.
Parent changes appear on the child, original parent and final parent, omitting nulls; the child
shows a parent transition while parents show the child added or removed. Blocking changes appear
on both endpoints, including across Projects, as Blocks or Blocked by according to direction.
Creating a Task with an initial parent records creation only on the new Task.

Consolidation uses the child ID plus parent field, or the ordered blocker/blocked pair, independently
of which interface initiated the change. Same-account edits within a sliding 60 seconds merge:
A → B → C shows the event only on the child, A and C; B's entry disappears. Returning to A
removes the event everywhere. Blocking add/remove cancellation likewise removes both appearances.
Historical endpoint titles and Project labels are retained; later linked Task name/status changes
do not create new entries or rewrite those snapshots. A List move records history on the moved
Task only. List reparenting and Project/List renames do not create Task ownership events.
See the [Activity contract](mvp-spec.md#task-comments-and-activity) for consolidation and attribution.

## Deletion rules

Deleting a Task always removes its incident Blocks / Blocked by records. Parent-child
requires the following explicit choice when the deleted Task has children:

1. **Recursively delete child subtree:** delete every descendant Task and all incident
   relationships. The impact confirmation enumerates the full affected set.
2. **Preserve child subtree:** remove the deleted Task and reparent only its direct children to the
   deleted Task's parent. If the deleted Task had no parent, reparent those children to the Project
   level. Each child retains its existing descendants and List ownership.

Deleting a Project deletes its owned Tasks and internal relationships. For cross-Project Blocks /
Blocked by records, it removes the edge but preserves the Task in the other Project.
The detailed deletion warning explicitly includes those externally affected relationship removals.

## Exclusions

- Cross-Project parent-child.
- More than one parent for a child, self-relationships, cyclic Blocks, and cyclic parent-child.
- Automatic lifecycle transitions or hard dependency gates. The Done warning is the sole MVP
  relationship-driven lifecycle intervention.
