# Taskman — Domain Model and Glossary

## Ownership and organization

- **Project**: a machine-independent top-level work container identified by its ID and name. It owns nested Lists and Tasks, and has no directory.
- **List**: a nested organizational container within exactly one Project. Lists may contain nested Lists and Tasks.
- **Direct Project Task**: a Task owned directly by its Project rather than by a List; it has list depth zero.
- **Owning location**: the single place where a Task resides: either its Project or one List within that Project.

## Ownership rules

- A Task belongs to exactly one Project and exactly one owning location at a time.
- A Task may move between its Project and any List within that same Project.
- A Task cannot belong to multiple Lists or span Projects in the MVP.
- A List has exactly one parent: its Project or another List in that Project. List nesting is unbounded and acyclic.

## Agent work

Agent Session implementation and provider rules are deferred to a separate accepted design. The
current Project model does not define a local execution directory. Agent work cannot by itself
complete a Task; a human explicitly changes Task state.

## Deletion

- A Project, List, or Task may be permanently deleted even when it contains nested Lists and Tasks.
- Deletion is recursive: removing a Project removes all its Lists and Tasks; removing a List removes its descendants.
- Before any recursive deletion, the product shows a detailed warning of the affected records and requires a second explicit confirmation button.
- Deleting a Task with children offers a choice: recursively delete its child subtree, or preserve it
  by reparenting only its direct children to the deleted Task's parent (or the Project-level Task
  hierarchy when it has no parent). Reparented children retain their own descendants and List ownership.
- A deletion removes every incident Task relationship. When a Project is deleted, cross-Project
  relationship edges are removed but externally owned Tasks are preserved and shown in the impact warning.

## Checklists

- **Checklist**: an ordered set of completion markers belonging to a Task.
- Checklist progress is informational only. Unchecked items never prevent a Task from entering **In Review** or **Done**.

## Task relationships

- **Task relationship**: an explicit association between two Tasks. Task relationships are in MVP scope and are distinct from List ownership.
- MVP relationship vocabulary includes directed **Blocks / Blocked by**, symmetric **Relates to**, and **parent-child** relationships.
- **Blocks / Blocked by** may span Projects. It is directed, has at most one edge per ordered Task
  pair, rejects self-links and all cycles, and counts as resolved for the **Done** warning only when
  the blocking Task is **Done** or **Will Not Do**. Moving a Task with unresolved blockers to **Done**
  requires warning confirmation.
- **Relates to** may span Projects. It is symmetric, permits at most one relationship per unordered
  Task pair, and rejects self-links.
- Parent-child is an acyclic, same-Project work-breakdown relationship: one parent may have many
  children, while a child has at most one parent. It does not change a Task's List ownership or status.
- Different relationship types may coexist between the same Tasks. A child may block its parent, but
  a parent may never block its own child.

## Task lifecycle

- **Task**: a unit of intended work. It is complete only when a human explicitly marks it **Done**.
- **Icebox**: intentionally deferred work that is not currently ready to begin.
- **Pending**: work that is ready to be started.
- **In Progress**: work actively being performed by a person, an external agent, or both.
- **In Review**: work awaiting human assessment before it can be completed.
- **Done**: the terminal state for work a human has reviewed and explicitly accepted as complete.
- **Will Not Do**: the terminal state for work intentionally abandoned or declined.

## Task creation

- A new Task requires a non-empty title and defaults to **Pending**. A user may explicitly select
  another lifecycle state during creation.
- Rich-text description, due date, and checklist are optional when creating a Task. Priority defaults to **None**.

## Priority

- **Priority**: a required, single-valued Task attribute with one fixed value: **None**, **Low**, **Medium**, **High**, or **Urgent**.
- **None** is the sole representation of a Task that has not been prioritized; priority is never unset.
- Priority does not change Task-state transitions or automatically order work.

## Due date-time

- **Due date-time**: an optional local date and time associated with a Task.
- The MVP uses the local system timezone only. Timezone selection and behavior after a timezone change are not specified.
- A due date-time is a planning signal only; it does not automatically change Task status.

## Lifecycle rules

- The normal forward flow is **Icebox → Pending → In Progress → In Review → Done**.
- A Task in **In Review** may return to **In Progress** or **Pending** when further work or reprioritization is needed.
- External agent work never completes a Task by itself.
