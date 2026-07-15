# Task Relationship Semantics and Constraints

**Resolved:** 2026-07-14

## Vocabulary and records

The MVP has three explicit Task relationship types. They are independent of a Task's owning
location: every Task remains directly owned by its Project or one List, even when it has a parent
or relationships across Projects.

| Type | Meaning | Scope | Cardinality and invariants |
| --- | --- | --- | --- |
| **Blocks / Blocked by** | A directed prerequisite. If A blocks B, B is blocked by A. | May cross Projects. | One edge per ordered Task pair; no self-edge and no cycle, including a cross-Project cycle. |
| **Relates to** | A symmetric contextual association. | May cross Projects. | One relationship per unordered Task pair; no self-edge. |
| **parent-child** | A work-breakdown relationship. | Same Project only. | A parent has many children; a child has at most one parent; the hierarchy is acyclic. |

A relationship type does not exclude another type for the same Task pair. For example, related
Tasks may also have a dependency. The one exception is that a parent may **never** block its own
child. A child may block its parent if the global Blocks graph remains acyclic.

## Lifecycle behavior

Task relationships never automatically alter Task state.

- Every lifecycle transition remains available to a human.
- When a user moves a Task to **Done** while it has one or more unresolved direct blockers, the
  product warns and requires explicit confirmation.
- A direct blocker is resolved for that warning when its Task is either **Done** or **Will Not Do**.
  No relationship state is changed automatically.

## Creation and display rules

- Relationship creation rejects invalid scope, self-links, duplicate edges, forbidden parent-to-child
  dependency direction, and a resulting Blocks or parent-child cycle before persisting anything.
- A directed Blocks record is presented from either endpoint as **Blocks** or **Blocked by**. A
  Relates to record is presented identically from either endpoint.
- The Task detail hierarchy displays only parent-child. Its Related Tasks table displays Blocks /
  Blocked by and Relates to. Cross-Project entries identify their other Project.

## Deletion rules

Deleting a Task always removes its incident Blocks / Blocked by and Relates to records. Parent-child
requires the following explicit choice when the deleted Task has children:

1. **Recursively delete child subtree:** delete every descendant Task, its Agent Sessions, and all
   incident relationships. The impact confirmation enumerates the full affected set.
2. **Preserve child subtree:** remove the deleted Task and reparent only its direct children to the
   deleted Task's parent. If the deleted Task had no parent, reparent those children to the Project
   level. Each child retains its existing descendants and List ownership.

Deleting a Project deletes its owned Tasks and internal relationships. For cross-Project Blocks /
Blocked by or Relates to records, it removes the edge but preserves the Task in the other Project.
The detailed deletion warning explicitly includes those externally affected relationship removals.

## Exclusions

- Cross-Project parent-child.
- More than one parent for a child, self-relationships, cyclic Blocks, and cyclic parent-child.
- Automatic lifecycle transitions or hard dependency gates. The Done warning is the sole MVP
  relationship-driven lifecycle intervention.