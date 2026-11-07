# Taskman — Domain Model and Glossary

## Ownership and organization

- **Project**: a machine-independent top-level work container identified by its ID and name, with a description, icon, and color. It owns nested Lists and Tasks, and has no directory.
- **List**: a nested organizational container within exactly one Project. Lists may contain nested Lists and Tasks.
- **Direct Project Task**: a Task owned directly by its Project rather than by a List; it has list depth zero.
- **Owning location**: the single place where a Task resides: either its Project or one List within that Project.

## Project identity

Project names are trimmed and non-empty. Descriptions are trimmed, may be empty, and contain at
most 160 characters; an empty description has no subtitle in the selector. Identity is stored on
the Project and shared by browser, API, and CLI creation and editing.

The default icon is `briefcase`. Allowed icon keys are `check-circle`, `folder`, `briefcase`,
`code-bracket`, `rocket-launch`, `beaker`, `light-bulb`, and `wrench-screwdriver`. Colors use an
uppercase `#RRGGBB` value, defaulting to `#6366F1`. The browser supports preset shortcuts and any
valid six-digit custom hex color. Description, icon, and color are never null.

The desktop navigation sidebar grows smoothly from 304 CSS px at a 1600 CSS px viewport to a
480 CSS px maximum at 2240 CSS px, giving long List names more room on wide screens.

The sidebar shows one active Project and its Lists. Explicit Project and List URLs select their
Project; opening `/` restores a valid Project remembered by this browser, otherwise it shows a
neutral selector. Include child Lists and Statuses are browser preferences retained across
navigation. Share copies the current route with both filters as an explicit snapshot. See the
[active Project selector specification](../specs/2026-09-22-active-project-selector-design.md) for
navigation, filter, validation, and API/CLI contracts.

## Task view counts and defaults

Project-task and List navigation rows show the number of Tasks matching the current global
Statuses and Include child Lists filters, including zero. The selected location's header shows
matching / eligible Tasks. Eligible Tasks include all unclosed Tasks plus Tasks in whichever
closed statuses are selected; this is a population comparison, not completion progress.

Both numbers use the same owning-location scope: direct Tasks when Include child Lists is off,
or that location and all descendant Lists when on. Task parentage and sidebar disclosure do not
change this scope. Filters apply to unselected sidebar rows too, and the selected row's count
matches the header's first number. Counts refresh with the existing workspace updates.

Fresh status selection includes Icebox, Pending, In Analysis, In Progress, and In Review;
Include child Lists defaults to off. Explicit URL filters override saved browser selections, which override defaults.
Saved Done and empty selections remain valid and are preserved without migration.

Badges remain aligned across List nesting. On hover-capable devices, row hover or keyboard focus
replaces the sidebar badge with the row's actions; names remain stationary and every action stays
keyboard reachable. Non-hover devices show badges and actions together. The header count remains
visible and explains both numbers through discoverable and accessible text. See the
[counting and presentation contract](../specs/2026-10-26-list-task-counts-design.md) for exact semantics.

## CLI connection context

A **CLI host** is a locally registered name for a Taskman backend URL and optional credential in
protected user configuration. A **repository binding** is a clone-local host name and Project ID
stored in Git's common directory, shared by linked worktrees and independent across clones.
It supplies the default CLI connection and Project scope without adding a directory to the
server-side Project model, changing ownership, or granting execution/lifecycle authority. See the
[named-host and repository-binding contract](../specs/2026-09-30-repository-backends-design.md)
and [CLI setup and recovery](../../README.md#local-cli).

## Ownership rules

- A Task belongs to exactly one Project and exactly one owning location at a time.
- A Task may move between its Project and any List within that same Project.
- A Task cannot belong to multiple Lists or span Projects in the MVP.
- A List has exactly one parent: its Project or another List in that Project. List nesting is unbounded and acyclic.

## Agent work

Agent Session implementation and provider rules are deferred to a separate accepted design. The
current Project model does not define a local execution directory. Explicit assignment to a
nonterminal Task authorizes an agent to make bounded updates within its agreed scope, append
evidence comments and explicitly move it to In Progress or In Review after inspecting the Task,
thread and blockers. Assignment is operator authorization; registration and runtime events are not.
Done, Not Planned, Will Not Do, reopening terminal work, blocker overrides, deletion and material
scope/priority changes require explicit operator decisions. These are operating rules, not new API
authorization enforcement or automatic transitions. In Progress is not an atomic claim; coordinate ownership.

## Deletion

- A Project, List, or Task may be permanently deleted even when it contains nested Lists and Tasks.
- Deletion is recursive: removing a Project removes all its Lists and Tasks; removing a List removes its descendants.
- Before any recursive deletion, the product shows a detailed warning of the affected records and requires a second explicit confirmation button.
- Deleting a Task with children offers a choice: recursively delete its child subtree, or preserve it
  by reparenting only its direct children to the deleted Task's parent (or the Project-level Task
  hierarchy when it has no parent). Reparented children retain their own descendants and List ownership.
- A deletion removes every incident Task relationship. When a Project is deleted, cross-Project
  relationship edges are removed but externally owned Tasks are preserved and shown in the impact warning.
- Deleting a Task also deletes its comments. Recursive Project, List, and child-Task deletion has the
  same effect for every deleted Task. Reparented children retain their comments.

## Checklists

- **Checklist**: an ordered set of completion markers belonging to a Task.
- Checklist progress is informational only. Unchecked items never prevent a Task from entering **In Review** or **Done**.

## Task comments and Activity

- **Task comment**: an append-only plain-text record attached to exactly one Task. It has a
  server-assigned ID and UTC creation time, normalized nonempty text of at most 10,000 Unicode
  grapheme clusters, the authenticated posting account while that account exists, and optionally a
  normalized custom display name of at most 80 grapheme clusters. The thread is ordered by
  `(created_at, id)` ascending. Comments can be posted at any Task status.
- **Posting account**: the authenticated browser user or API key owner who creates a comment.
  The account is selected by authentication, never by submitted comment data. Its current login
  email appears on reads. A custom name labels the comment separately, displayed as
  `Name (login)`; without a name, only `login` appears. The name neither impersonates the account
  nor grants authority.
- Account deletion nulls the comment's posting-account link but retains its text, creation time,
  and custom name. Its login then appears as `Deleted user`, with no email snapshot. Changing an
  account email changes the login shown on its earlier comments.
- **Activity**: the Task-detail tab that currently contains the comment thread and composer.
  The Sessions tab shares its panel and remains a truthful empty state. This presentation replaces
  the earlier stacked Activity and Sessions sections. Task changes do not generate Activity entries.
- A successful comment advances its Task's `updated_at` to at least the comment time, without
  changing `lock_version`, Task fields, or status. A failed post leaves the Task unchanged. Thus
  `updated_at` records the latest direct Task mutation or successful comment; it is not proof of
  human acceptance. Comments do not attribute Task edits, imply lifecycle approval, or establish
  per-user ownership.

## Task relationships

- **Task relationship**: an explicit association between two Tasks. Task relationships are in MVP scope and are distinct from List ownership.
- MVP relationship vocabulary includes directed **Blocks / Blocked by** and **parent-child**
  relationships. Symmetric **Relates to** links are deferred beyond the MVP; see the
  [relationship contract](relationships.md).
- **Blocks / Blocked by** may span Projects. It is directed, has at most one edge per ordered Task
  pair, rejects self-links and all cycles, and counts as resolved for the **Done** warning only when
  the blocking Task is **Done**. Not Planned and Will Not Do remain unresolved. Moving a Task with
  unresolved direct blockers or eligible unfinished descendants to **Done** requires one combined
  warning confirmation; see the [descendant Done warning design](../specs/2026-11-07-descendant-done-warning-design.md).
- Parent-child is an acyclic, same-Project work-breakdown relationship: one parent may have many
  children, while a child has at most one parent. It does not change a Task's List ownership or status.
  The Done warning visits non-resolving descendants across Lists, stopping before each resolving
  descendant and its subtree. Only Done currently resolves; Not Planned and Will Not Do do not.
  Direct blockers are checked independently, including those below a resolving descendant.
- Parent-child and Blocks may coexist between the same Tasks. A child may block its parent, but
  a parent may never block its own child.

## Task lifecycle

- **Task**: a unit of intended work. It is complete only after explicit human acceptance and a
  human-authorized transition to **Done**.
- **Icebox**: intentionally deferred work that is not currently ready to begin.
- **Pending**: work that is ready to be started.
- **In Analysis**: work being investigated or clarified before implementation begins.
- **In Progress**: work actively being performed by a person, an external agent, or both.
- **In Review**: work awaiting human assessment before it can be completed.
- **Done**: the terminal state for work a human has reviewed and explicitly accepted as complete.
- **Not Planned**: work we currently consider unnecessary, retained for reconsideration if the need arises.
- **Will Not Do**: the terminal state for work explicitly abandoned or declined by the operator.

The canonical selector and status-sort order is Icebox, Pending, In Analysis, In Progress, In Review,
Done, Not Planned, Will Not Do; storage/API values are `icebox`, `pending`, `in_analysis`,
`in_progress`, `in_review`, `done`, `not_planned`, `will_not_do`. Done, Not Planned and Will Not Do are closed for counting;
only Done is completed and resolves blockers. Explicitly selected closed states participate in
visible and eligible counts. Choosing Not Planned requires an explicit operator decision.

In Analysis and Not Planned are additional states. In Analysis counts as open and is included in
fresh default filters; existing explicit saved selections or URL snapshots that omit it remain
unchanged. Browser selectors offer default states plus additional states represented in their
unfiltered scope. Main-listing and form selectors retain page observations
until reload/remount. Main-listing scope follows owning location and Include child Lists; form
scope is the direct owning List or the whole Project when no List is selected. Hierarchy availability
is derived from the complete currently loaded connected tree.
Remembered filtering choices survive when an option is temporarily absent. Hierarchy choices start
with every supported state checked and last for one open detail dialog.

Enhanced form pickers always retain their selected value and can reveal hidden states through
**Show additional states** inside the popup. Reveal keeps the popup open and value unchanged,
survives Task navigation in the same edit dialog, and resets for a new creation form or reopened
dialog. Native fallback offers all supported real states directly. State options and main-table badges
show delayed descriptions; see the [additional-state contract](../specs/2026-11-01-additional-task-states-design.md)
for exact scopes, lifetimes, accessibility, delivery limits and filter compatibility.

State picker labels (including its selected trigger value) and both status filters use the same
identifying state hues as badges. Unselected/unchecked additional-state options remain subdued.
Selected/checked and keyboard-focus indicators remain independent of the text color.

## Task creation

- A new Task requires a non-empty title and defaults to **Pending**. A user may explicitly select
  another lifecycle state during creation.
- Markdown description, due date, and checklist are optional when creating a Task. Priority defaults to **None**.

## Task description

A Task description is a non-null Markdown string; clearing it stores `""`. API and CLI values use
that same string. Existing descriptions are interpreted as Markdown without rewriting stored text
on open, focus or departure. Actual edits may normalize Markdown spelling while preserving meaning
and formatting, subject to the temporary limitations below.

Creation and detail share an always-editable hybrid editor, formatting toolbar and source mode.
Creation persists only on **Create task**; existing Tasks use field-specific autosave. Capture of
current browser input precedes creation, in-app Task departure and missing-location recovery. A failed
capture retains the draft and blocks the transition until retry. Recovery initializes sanitized
Markdown read-only, with selection and the existing Copy actions available.

The editor starts around ten lines high and supports native vertical resizing. Double-clicking
the resize corner fits its content, with the starting height as the minimum. Source and hybrid
views retain their own sizes; typing and ordinary server updates preserve a manually chosen height.

Current editor limitations are accepted temporarily with corrections planned:

- Table and strikethrough Markdown can render literally.
- Editing another paragraph can escape strikethrough markers into literal tildes.
- Editing another paragraph can remove existing raw HTML audio/video references.
- An unrelated edit can remove leading indentation from the first line of fenced code.
- Typing opening code fences in hybrid mode escapes them; use the Code block toolbar action or source mode.

Untouched descriptions are not rewritten. These exact defects do not authorize other destructive
source conversions. In-app navigation is captured; leaving or reloading the whole document can
discard unsent description text, and the existing comment unload warning is best-effort. See the
[Markdown description design](../specs/2026-11-06-task-description-markdown-design.md)
for exact reproductions, follow-up requirements and replacement-history behavior.

## Priority

- **Priority**: a required, single-valued Task attribute with one fixed value: **None**, **Low**, **Medium**, **High**, or **Urgent**.
- **None** is the sole representation of a Task that has not been prioritized; priority is never unset.
- Priority picker options and the selected trigger value use the existing priority badge text colors.
- Priority does not change Task-state transitions or automatically order work.

## Due date-time

- **Due date-time**: an optional local date and time associated with a Task.
- The MVP uses the local system timezone only. Timezone selection and behavior after a timezone change are not specified.
- A due date-time is a planning signal only; it does not automatically change Task status.

## Lifecycle rules

- The normal forward flow is **Icebox → Pending → In Progress → In Review → Done**, with optional
  **In Analysis** between Pending and In Progress; no transition through it is required.
- A Task in **In Review** may return to **In Progress** or **Pending** when further work or reprioritization is needed.
- External agent work never completes a Task by itself.
- Assignment may authorize explicit In Progress/In Review transitions under the [agent-work
  contract](#agent-work); terminal decisions remain with the operator.
