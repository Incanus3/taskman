# Lists and Nested Organization Design

**Status:** Approved  
**Date:** 2026-08-26

## Context

Taskman currently supports Projects, direct Project Tasks, full Task creation and editing, explicit
human-controlled lifecycle changes, and URL-backed Task detail over the preserved Project Task
list. The delivered Task-detail shell already contains the future hierarchy, Activity, and Sessions
regions, but the workspace sidebar still contains only a flat Project list and every Task is owned
directly by its Project.

The authoritative product documents require:

- an unbounded acyclic tree of Lists within each Project;
- one owning location per Task: its Project or one List in that Project;
- direct Tasks by default, with optional descendant inclusion that preserves each Task's source
  List;
- Task movement only within the owning Project; and
- permanent recursive List deletion with impact safeguards.

This design delivers List organization and Task movement. Recursive List deletion is intentionally
deferred to the deletion-safeguards slice so Taskman never exposes an unsafe temporary deletion
contract.

The implementation baseline is upstream commit `7c8028b0550f880d8b9fa56bd852ca9fbf6d4247`,
which completed Task detail and navigation. At design time the repository has no active delivery
Bead for this slice.

## Outcome

A user can create and rename nested Lists, select a Project or List in a URL-backed workspace,
optionally include Tasks from descendant Lists, create a Task in the selected location, and
explicitly move a Task to another location in the same Project.

Success means:

- the sidebar presents a usable Projects-and-Lists tree;
- direct and descendant Task views are truthful and reproducible from the URL;
- every included Task identifies its owning location;
- Task movement requires a deliberate destination choice and explicit submission;
- invalid or stale identifiers cannot cross Project boundaries;
- existing Project and Task-detail behavior remains intact; and
- focused tests, the repository verification gate, and responsive browser acceptance pass.

## Scope

### Included

- Persisted, arbitrarily nested Lists.
- List creation under a Project or another List.
- List renaming.
- Stable List-tree ordering.
- Canonical List-selection routes.
- URL-backed descendant inclusion for Project and List selections.
- Task creation in the selected Project or List.
- Explicit Task movement between valid locations in one Project.
- Full owning paths in descendant Task results and move destinations.
- Recoverable validation, lookup, and persistence failures.
- Focused context, constraint, component, LiveView, and browser verification.

### Excluded

- List deletion, including empty-List deletion.
- Moving or reordering Lists.
- Manual ordering of Lists or Tasks.
- Drag-and-drop.
- Cross-Project Task movement.
- Task relationships, checklists, Activity records, and Agent Session behavior.
- Persisted expansion or descendant-inclusion preferences.
- Search, filtering, pagination, or saved views beyond destination search in the move popover.
- Project editing or deletion.

## Decisions

### Deletion is deferred

The roadmap previously listed List deletion in this slice while reserving recursive impact warnings
and confirmation for the deletion-safeguards slice. List deletion is removed from this slice. The
later deletion work will add the complete recursive contract rather than shipping an interim
empty-only or unsafe behavior.

### Lists use parent links

Each List stores its immediate parent List, or no parent when it is directly under the Project. This
adjacency-list representation supports unbounded nesting without the write complexity of a
materialized path or closure table.

List parentage is immutable in this slice. A newly created List can point only to an existing List
in the same Project, so application-created trees remain acyclic. A future List-movement feature
would require explicit cycle detection before parentage becomes editable.

### Lists have a dedicated context

`Taskman.Lists` owns List persistence, validation, lookup, tree assembly, paths, and descendant
discovery. `Taskman.Tasks` continues to own Task creation, queries, editing, and movement. The web
layer calls only public context APIs and does not use `Taskman.Repo` or construct Ecto queries.

This keeps Project identity and directory rules in `Taskman.Projects`, List-tree concerns in
`Taskman.Lists`, and Task ownership changes in `Taskman.Tasks`.

### Movement is explicit

Task location is not another autosaved Task-edit field. A user opens a dedicated move popover,
selects a destination, and submits the move explicitly. Both a Task row and Task detail expose the
same reusable interaction.

## Persistence model

### TaskList

The `Taskman.Lists.TaskList` schema maps to the new `lists` table:

| Column | Contract |
| --- | --- |
| `id` | Primary key. |
| `project_id` | Required Project foreign key. Project deletion cascades to TaskLists. |
| `parent_list_id` | Optional immediate parent TaskList. |
| `name` | Required string, trimmed before validation, maximum 255 characters. |
| timestamps | UTC timestamps matching existing schemas. |

Sibling TaskList names are unique with case-insensitive comparison. The stored name retains the
user's casing. One PostgreSQL expression index enforces the rule:

```sql
CREATE UNIQUE INDEX lists_sibling_name_index
ON lists (project_id, parent_list_id, lower(name))
NULLS NOT DISTINCT;
```

Consequently, sibling names such as **Planning** and **planning** conflict, while the same name may
appear under different parents. `NULLS NOT DISTINCT` makes root TaskLists share one effective parent
value for uniqueness instead of treating each `NULL` as different. The changeset maps
`lists_sibling_name_index` to the `name` field.

This index syntax requires PostgreSQL 15 or newer. Taskman's pinned CI service uses PostgreSQL 18.4,
and the repository's standard `postgres:alpine` helper follows a compatible current release.

The database has a unique supporting key on `(id, project_id)`. The composite parent foreign key
`(parent_list_id, project_id) → lists(id, project_id)` guarantees that a parent TaskList belongs to
the same Project. It uses PostgreSQL's default end-of-statement `NO ACTION` behavior: direct parent
deletion is blocked while a coherent Project-wide deletion may remove all dependent rows in one
operation. The application does not expose TaskList deletion or parent updates in this slice.

TaskList sibling order is stable oldest-first by `inserted_at`, then `id`. The tree is assembled
from one ordered Project-scoped query rather than one query per node.

Callers alias the schema as `TaskList` to avoid conflict with Elixir's standard `List` module.

### Task location

The `tasks` table gains nullable `list_id`:

- `NULL` means direct Project ownership;
- a TaskList ID means the Task is owned by that TaskList.

Existing Tasks are valid without a data rewrite because the new column defaults to `NULL`.

A unique supporting key on `lists(id, project_id)` and a composite Task foreign key
`(list_id, project_id) → lists(id, project_id)` enforce same-Project ownership. TaskList deletion is
blocked by the default `NO ACTION` behavior while dependent Tasks remain. The existing
`tasks.project_id` remains required and continues to identify the Task's permanent owning Project.

`list_id` is never cast from ordinary Task form parameters. Task creation and movement set it
programmatically through public context functions.

## Context contracts

Function names may be refined mechanically during implementation, but their behavior and return
semantics are fixed by this design.

### `Taskman.Lists`

- `list_lists_for_project(project)` returns every List in stable sibling order, with enough parent
  data to assemble the tree without additional queries.
- `get_list_for_project(project, id)` accepts a positive integer or fully parseable positive-integer
  string and returns the matching List or `nil`.
- `create_list(project, parent_or_nil, attrs)` creates a root or child List. A parent outside the
  Project returns `{:error, :not_found}`. Validation and uniqueness failures return
  `{:error, changeset}`.
- `change_list(list, attrs)` builds the create or rename form changeset without permitting
  `project_id` or `parent_list_id` changes.
- `rename_list(project, list, attrs)` returns `{:ok, list}`, `{:error, changeset}`, or
  `{:error, :not_found}` for mismatched ownership.
- Tree/path helpers return deterministic root-to-node paths used by navigation, location labels,
  and destination search.

There is no delete, reparent, reorder, or move API.

### `Taskman.Tasks`

- Existing direct-Project creation remains supported.
- List-owned creation accepts a Project and validated destination List, sets both ownership fields
  programmatically, and returns `{:error, :not_found}` for a cross-Project destination.
- `list_tasks_for_location(project, location, include_descendants: boolean)` returns
  `{:ok, listed_tasks}` for a direct Project or validated List location and
  `{:error, :not_found}` for mismatched ownership.
- Each listed result is a `Taskman.Tasks.TaskWithLocation` read model containing the Task and its
  owning path. Direct Project Tasks use an empty List path; List Tasks use the full root-to-owner
  List path.
- Results retain the current global Task ordering: `inserted_at`, then `id`.
- `move_task(project, task, destination_or_nil)` returns:
  - `{:ok, task}` after a valid same-Project move;
  - `{:error, :not_found}` for a mismatched Task or destination;
  - `{:error, :unchanged_location}` when the persisted destination is already current; or
  - `{:error, changeset}` for a persistence constraint failure.

Descendant Task queries use a PostgreSQL recursive query rooted at the selected Project or List.
Ordinary tree rendering does not require a recursive database query.

## Routes and URL state

| Browsing context | Route |
| --- | --- |
| Direct Project Tasks | `/projects/:project_id` |
| Create direct Project Task | `/projects/:project_id/tasks/new` |
| Project-context Task detail | `/projects/:project_id/tasks/:task_id` |
| Direct List Tasks | `/projects/:project_id/lists/:list_id` |
| Create direct List Task | `/projects/:project_id/lists/:list_id/tasks/new` |
| List-context Task detail | `/projects/:project_id/lists/:list_id/tasks/:task_id` |

`include_children=true` is the only query value that enables descendant inclusion. Missing or other
values behave as false. Links generated after an unrecognized value canonicalize it away.

All open, close, create, cancel, and Task-detail transitions preserve the selected location and the
recognized descendant query. Back, forward, refresh, and shared links therefore reproduce the same
Task table.

A Task-detail route accepts any Task in the selected Project even when it is not currently present
in the background result set. This is required so an open Task remains visible after it moves out
of that result set. A Task from another Project remains not found.

An invalid Project renders the existing recoverable Project-not-found state. An invalid or
cross-Project List renders a recoverable location-not-found state while preserving the usable
sidebar. An invalid or cross-Project Task renders the existing Task-not-found modal over the valid
browsing context.

## Navigation tree

The sidebar becomes a semantic Projects-and-Lists tree. Project and List selection are distinct
from expansion:

- selection uses URL patches and exposes `aria-current="page"`;
- expansion buttons expose `aria-expanded`;
- node actions use separately labelled buttons;
- selected ancestors are forced open so the selected node remains visible;
- the selected node may collapse its own descendants;
- expansion choices for other branches last only for the mounted workspace LiveView.

The LiveView renders a flattened stream of visible navigation nodes. Each item contains a stable
DOM identity, kind, depth, expansion state, and selection state. Semantic `tree` and `treeitem`
roles plus `aria-level` preserve the hierarchy without nesting regular assigned collections.
Creating a List expands its parent and inserts the new node while leaving the selected location
unchanged. Renaming preserves selection and expansion.

Project actions offer **Add List**. List actions offer **Add child List** and **Rename**. These
actions open compact anchored popovers with forms driven by `to_form/2` and the project-owned input
component. One socket-free `%TaskmanWeb.ProjectLive.ListEdit{}` value owns the active Project, create/rename
target, form, validation, stale-target revalidation, and node-placement policy. There is no
deletion affordance.

## Task table behavior

Direct Tasks are the default for both Project and List locations.

**Include child Lists** appears at both levels and patches the recognized URL query. When enabled:

- a Project view includes direct Project Tasks and Tasks from all of its Lists;
- a List view includes its direct Tasks and Tasks from every descendant List;
- each row displays a Location column;
- a direct Project Task is labelled **Project**;
- a List Task is labelled with its complete List path, such as
  **Marketing / Launch / Copy**.

The Task table remains a LiveView stream and resets after location changes, descendant-query
changes, List mutations that alter displayed paths, and Task moves. Empty-state and count state are
tracked separately from the stream.

**Add Task** always creates in the selected location, even when descendant inclusion is active.
After creation, the route returns to the same location with the recognized query preserved.

## Explicit Task movement

Every Task row and the Task-detail surface expose a **Move Task** button. Both use the same
`TaskMove.Popover` function component and server-side event contract. Only one move surface is
active at a time.

The anchored popover contains:

- a searchable, accessible destination combobox;
- a **Project · _project name_** direct-location option;
- every List labelled by its complete root-to-node path;
- a clear indication of the current location;
- an explicit **Move Task** submit button disabled until a different destination is selected;
- a cancel action and local error region.

Destination search is case-insensitive substring matching over full displayed paths. Results retain
tree order. An empty search has an explicit no-results state. Search text and selected destination
are transient LiveView state and are discarded when the popover closes.

Opening the popover never mutates the Task. Submission revalidates the Task, destination, and
Project ownership against current persistence state.

For a Task-detail move, pending autosaved Task fields are flushed before location changes. If that
flush fails, movement does not occur and both the Task save error and move failure remain visible.
For a successful move:

- the Task table is re-queried and reset;
- the selected Project/List and descendant query do not change;
- the popover closes;
- a row that no longer belongs disappears;
- a row that remains visible receives its new location path; and
- open Task detail remains open with the moved Task as the selected Task.

The interaction should use ordinary LiveView events, `phx-click-away`, and keyboard events where
they suffice. A small colocated hook may be added only if browser focus or anchored-popover behavior
cannot be delivered accessibly with LiveView primitives; no general JavaScript framework or
external bundle is introduced.

## Web components and responsibilities

- `TaskmanWeb.ProjectLive` remains responsible for route interpretation, selected location,
  expansion state, persistence outcomes, streams, and event orchestration.
- `TaskmanWeb.ProjectLive.ListEdit` owns the transient inline List form interaction and revalidates its target
  through public List context APIs.
- A project-owned `WorkspaceNavigation` function-component module renders the tree and List
  management surfaces from the navigation stream and one `%ListEdit{}` value.
- The presentation-only `TaskMove.Popover` function component renders the shared move interaction
  from one `%TaskMove{}` value.
- `TaskComponents.row` receives location presentation data and the move entry point.
- Existing Task form and Task-detail components retain their editing responsibilities.
- No LiveComponent is introduced unless implementation demonstrates a concrete requirement for
  independently managed lifecycle or state.

This is a targeted extraction: it prevents the existing LiveView template from absorbing the new
tree and move rendering, without refactoring unrelated Task autosave behavior.

## Error behavior

- Blank, overlong, or case-insensitively duplicate sibling List names render inline changeset
  errors and keep the List popover open.
- Malformed, stale, or cross-Project parent and destination identifiers return controlled
  not-found behavior and never mutate persistence.
- A stale current destination is detected on submission; unchanged movement leaves the Task intact
  and reports that it is already in that location.
- A failed move keeps the popover open, preserves the table, and displays an actionable local error.
- A failed Task-detail autosave prevents movement rather than silently discarding or bypassing the
  invalid draft.
- A successful List rename refreshes every visible breadcrumb, tree label, Task location path, and
  move destination derived from it.
- Database constraints are translated into the corresponding changeset or controlled context error
  rather than leaking exceptions through the LiveView.

## Testing and verification

### Persistence and context tests

- List schema validation and protected ownership fields.
- Root and child creation, arbitrary-depth paths, stable sibling ordering, and renaming.
- Duplicate root and nested sibling rejection, including names that differ only by case.
- Database rejection of cross-Project parents.
- Database rejection of cross-Project Task/List ownership.
- Existing Tasks remaining direct Project Tasks after migration.
- Direct Project, direct List, Project-descendant, and List-descendant queries.
- Full owning paths and stable Task ordering.
- Direct and List-owned Task creation.
- Valid movement among Project and List locations.
- Unchanged, stale, and cross-Project move rejection.
- Absence of public List deletion or reparenting behavior.

### LiveView and component tests

- Semantic streamed tree nodes, expansion state, selected paths, and canonical List patches.
- Project/List creation and rename popovers, inline validation, and unique DOM IDs.
- Recoverable malformed and cross-Project List routes.
- Descendant query patching, refresh behavior, and Location-column results.
- Task creation in the selected location with descendant state preserved.
- Task-detail open/close behavior from both direct and descendant results.
- Searchable move destinations with full paths and explicit submission.
- Row-initiated and detail-initiated movement.
- Row disappearance, retained rows with updated paths, and open detail after movement.
- Move blocking when a dirty Task field cannot be saved.
- Existing autosave, lifecycle, invalid-Task-route, and responsive Task-detail regressions.

Tests use key DOM IDs and `Phoenix.LiveViewTest` selectors rather than raw HTML or styling
assertions.

### Final gates

- Focused context, migration, component, and LiveView tests.
- Asset compilation.
- `mix precommit`.
- Search confirming `TaskmanWeb` does not call `Taskman.Repo` or construct Ecto queries.
- Search confirming no List-delete or internal planning terminology reaches product surfaces.
- Responsive browser acceptance covering deep trees, long paths, List popovers, descendant tables,
  the move combobox, empty and failure states, and Task detail at wide and narrow viewports.
- Independent implementation verification before the slice is declared complete.

## Rejected alternatives

### Put Lists in `Taskman.Projects`

This would save one context initially but combine Project identity and filesystem rules with tree
traversal and Task-location coordination. The dedicated context has a clearer durable
responsibility.

### Materialized paths or a closure table

These representations optimize descendant reads at the cost of more write and migration machinery.
The MVP has no List movement, ordering, or analytics requirement that justifies that complexity.

### Autosave Task location

Location changes alter ownership and can remove the Task from the active result set. An explicit
action is easier to understand, confirm, retry, and test.

### Row-only movement or drag-and-drop

A row-only action would make movement unavailable from Task detail. Drag-and-drop adds substantial
JavaScript and accessibility work and still needs a non-drag alternative. The shared explicit
popover covers both contexts with one contract.

### Early List deletion

Empty-only deletion creates a temporary rule, while recursive deletion requires the safeguards
already assigned to a later slice. Deferring deletion preserves one coherent product contract.

## Expected file boundaries

Implementation is expected to touch or add files in these areas:

- migrations for `lists` and `tasks.list_id`;
- `lib/taskman/lists.ex` and focused modules under `lib/taskman/lists/`;
- `lib/taskman/tasks.ex` and `lib/taskman/tasks/task.ex`;
- `lib/taskman_web/router.ex`;
- `lib/taskman_web/live/project_live.ex` and its HEEx template;
- focused navigation and Task-move function components under
  `lib/taskman_web/components/`;
- fixtures and focused context, component, and LiveView tests.

Unrelated contexts, Agent Session work, relationship work, deployment configuration, and product
documents outside the clarified deletion sequencing should remain unchanged.

## Planning and next-session checklist

Before implementation:

1. The operator reviews and approves this written specification.
2. Write a detailed implementation plan in `docs/plans/` after reading this specification in full.
3. Create repository-local Beads for the reviewed implementation units.
4. Update the workstream handoff with the approved plan and active Bead IDs.
5. Continue implementation in a clean session, defaulting to delegated execution unless the
   operator chooses another approach.

Implementation should begin with tests for the persistence invariants and context contracts, then
proceed through routes and read behavior before List-management and Task-movement interactions.
