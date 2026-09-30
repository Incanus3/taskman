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
- An unconfirmed move to **Done** with one or more unresolved direct blockers warns and leaves
  status unchanged. The move requires explicit confirmation.
- API and CLI may express that confirmation as a case-specific, one-request force override without
  listing blocker IDs. When specific blocker IDs are confirmed, the Done write may proceed if the
  current unresolved direct blocker set is unchanged, smaller, or empty. A newly unresolved
  blocker absent from the confirmed IDs requires a fresh warning.
- A direct blocker is resolved for that warning when its Task is either **Done** or **Will Not Do**.
  No relationship state is changed automatically.
- A Task that remains Done needs no new warning for an unchanged status or an ordinary field edit.
  Explicit status requests retain optimistic conflict checks: a stale Done request cannot silently
  succeed after another user reopens the Task. A warning or conflict rejects all fields in the
  same update.

## Creation and display rules

- Relationship creation rejects invalid scope, self-links, duplicate edges, forbidden parent-to-child
  dependency direction, and a resulting Blocks or parent-child cycle before persisting anything.
- A directed Blocks record is presented from either endpoint as **Blocks** or **Blocked by**.
- The Task detail hierarchy displays only parent-child. Its Related Tasks table displays Blocks /
  Blocked by. Cross-Project entries identify their other Project.

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
