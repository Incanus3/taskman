# Parent-Child Task Hierarchy Design

**Status:** Approved  
**Date:** 2026-08-30

## Context

Taskman is a locally started, single-user Phoenix LiveView application. The delivered product
supports Projects, nested Lists, Tasks, URL-backed Task detail, same-Project Task movement, a
versioned JSON API, a Req-backed `taskman` CLI, shell completions, onboarding, and a bundled
agent skill.

The Task-detail modal already contains a collapsible hierarchy sidebar. It currently renders only
the selected Task and the truthful message `No parent or child Tasks`; there is no hierarchy
persistence or relationship operation. The API and CLI Task representations likewise have no
parent field.

The authoritative product rules define parent-child as:

- a same-Project, acyclic work-breakdown relationship;
- at most one parent for a child and any number of children for a parent;
- independent of Project/List ownership and lifecycle state; and
- distinct from Blocks / Blocked by and Relates to.

The API, CLI, and agent-skill slice established parity as a standing completion requirement:
meaningful persisted and query operations exposed in the UI ship together across the domain
context, browser, API, CLI, help, completions, bundled skill, and focused verification.

At design time the workspace is clean, the repository-local Beads store has no open or blocked
issues, and there is no active handoff. The roadmap identifies Task relationships as the next
slice. This design covers only its first increment: parent-child hierarchy.

## Outcome

A user can create a Task with an optional parent, create a subtask from an existing Task row, and
change or clear a Task's parent from the normal edit form. Task detail presents a navigable,
progressively expandable hierarchy without embedding relationship mutation controls in the tree.
The API and CLI expose the same parent field and a deterministic hierarchy query.

Success means:

- the database and domain layer prevent cross-Project parents, self-parenting, and cycles;
- List ownership remains independent of parentage;
- create and edit use the same searchable parent selector;
- the Add subtask shortcut reuses the normal create flow with a preselected parent;
- Task-detail hierarchy navigation preserves the current Project/List browsing context;
- hierarchy loading and mutations have complete API and CLI parity;
- failed parent changes are atomic and recoverable; and
- focused tests, the repository verification gate, independent verification, and responsive
  browser acceptance pass.

## Scope

### Included

- A nullable immediate-parent reference for Tasks.
- Parent selection during normal Task creation.
- Parent selection and removal during normal Task editing.
- An Add subtask action on every visible Task row.
- Project-wide, searchable parent candidates with disambiguating location and Task ID.
- Transactional same-Project, self-link, and cycle validation.
- A deterministic connected-hierarchy query.
- A navigable hierarchy tree in Task detail.
- Progressive expansion of branches outside the selected Task's initial context.
- `parent_task_id` in Task API representations and create/update payloads.
- A hierarchy API endpoint.
- CLI create/update parent options and a hierarchy command.
- Help, Bash/Fish completions, onboarding where relevant, bundled skill, and output-contract parity.
- Focused migration, context, component, LiveView, API, CLI, and end-to-end tests.

### Excluded

- Blocks / Blocked by and unresolved-blocker completion warnings.
- Relates to.
- Cross-Project parent-child.
- Relationship mutation controls inside the hierarchy sidebar.
- Drag-and-drop hierarchy editing.
- More than one parent for a Task.
- Manual sibling ordering.
- Bulk hierarchy operations.
- Project, List, or Task deletion.
- Subtree deletion and reparent-on-delete behavior.
- Persisting hierarchy expansion across LiveView sessions.
- Automatic lifecycle changes based on hierarchy.

## Decisions

### Parentage is editable in the normal Task form

The searchable parent selector appears in both create and edit modes. Parenting is therefore one
coherent Task-editing concept rather than separate create-only, row-only, and endpoint-only
operations.

Selecting or clearing a parent in edit mode autosaves with inline validation feedback, consistent
with the existing Task-detail editing experience. The hierarchy sidebar itself remains
relationship-read-only: it provides expansion and navigation, not mutation controls.

There is no dedicated Change parent row action, parent API subresource, or `tasks reparent` CLI
command. The ordinary Task update contract owns parent changes.

### Add subtask is a shortcut into normal creation

Each visible Task row exposes Add subtask. The action opens the existing Task-creation modal with:

- the source Task preselected as parent;
- blank ordinary Task fields with existing defaults; and
- the source Task's current Project/List location as the new Task's initial owning location.

The shortcut does not introduce a separate quick-create form. The parent remains editable in the
normal creation picker.

The inherited location is an initial default captured from the shortcut source. Changing or
clearing the parent selection does not silently change the new Task's List ownership because
ownership and hierarchy are independent concepts. The create modal's location copy identifies the
Project or List in which the Task will be created.

Normal New Task creation continues to use the currently browsed Project/List location and offers
the same optional parent picker.

### Hierarchy navigation preserves browse context

Opening another Task from the hierarchy replaces the selected Task in the existing URL-backed
modal while retaining:

- the current Project;
- the current selected List or Project root;
- the descendant-inclusion query state;
- the Task list behind the modal; and
- the session-scoped hierarchy-panel preference.

The target Task does not need to be present in the visible Task stream. Hierarchy navigation does
not automatically switch to the target Task's owning List.

### The initial tree reveals context without expanding every branch

Task detail loads the selected Task's entire connected parent-child tree from its topmost ancestor.
The initial visible shape forces open:

- every ancestor on the path to the selected Task; and
- the selected Task itself.

Expanding those nodes reveals the ancestor path, sibling branches at each path level, and the
selected Task's direct children. Other branches remain collapsed behind disclosure controls and
can be expanded in place. The selected Task remains visible and is marked as current.

Manual branch expansion is state for the currently open modal and connected hierarchy. It survives
following hierarchy links among Tasks in that same connected tree. Loading another Task in the
tree adds its required ancestor path to the visible set without discarding other manually expanded
branches.

Closing the Task-detail modal clears manual branch expansion. Opening a Task from another connected
hierarchy also starts with only that Task's required ancestor path and selected node expanded, even
if LiveView navigation happens to reuse the same process. Branch expansion is not stored in the
database or browser storage.

### Parent-child uses a nullable self-reference

The `tasks` table gains `parent_task_id`. This directly models the product cardinality that a Task
has zero or one parent while a parent has any number of children.

Blocks / Blocked by and Relates to will receive their own persistence design when those increments
become current. Their directionality, symmetry, cross-Project scope, cardinality, and cycle rules
do not justify a generalized relationship table now.

## Persistence model

### Task parent column

The migration adds:

| Database object | Contract |
| --- | --- |
| `tasks.parent_task_id` | Nullable immediate-parent Task ID. |
| `tasks_id_project_id_index` | Unique supporting key on `(id, project_id)`. |
| `tasks_parent_task_id_project_id_fkey` | Composite foreign key from `(parent_task_id, project_id)` to `tasks(id, project_id)`. |
| `tasks_parent_not_self_check` | Check constraint requiring `parent_task_id IS NULL OR parent_task_id <> id`. |
| `tasks_project_id_parent_task_id_index` | Lookup index on `(project_id, parent_task_id)`. |

The composite foreign key makes cross-Project parentage impossible even if application validation
is bypassed. The self check provides a second line of defense for the simplest cycle.

The parent foreign key uses `ON DELETE NO ACTION`. Task deletion is not available in this
increment. The later deletion-safeguards slice must replace or coordinate that behavior with its
explicit subtree-deletion and child-reparenting contract; this increment must not introduce an
implicit cascade or nullification rule.

### Schema and changesets

`Taskman.Tasks.Task` gains:

- `belongs_to :parent_task, Taskman.Tasks.Task`;
- the `parent_task_id` type field; and
- the corresponding foreign-key and check-constraint mappings.

`parent_task_id` is not added to the ordinary `cast/3` field list. Like Project and List ownership,
it is programmatically controlled after the context resolves identity and validates hierarchy
invariants. Create and update forms may carry a parent selection, but web params never gain direct
authority to set the foreign key through a generic changeset.

## Domain architecture

### Public context boundary

`Taskman.Tasks` remains the public domain boundary for browser and API clients. The web layer does
not call `Taskman.Repo`, construct Ecto queries, or duplicate cycle rules.

Existing create and update functions gain an optional resolved-parent input while retaining their
current call forms:

- create without a parent input creates a root Task;
- update without a parent input leaves parentage unchanged;
- explicit `nil` removes parentage; and
- a `%Task{}` from the same Project sets or replaces the parent.

The exact internal function arities may follow the existing context conventions, but callers must
not pass an unvalidated raw parent ID into the Task changeset.

`Taskman.Tasks` also exposes:

- Project-scoped parent-candidate search with optional current-Task exclusions; and
- Project-scoped hierarchy loading centered on one Task.

### Focused hierarchy module

A focused module under `Taskman.Tasks` owns hierarchy-specific query and projection behavior. Its
responsibilities are:

- ancestor and descendant discovery;
- ancestor-chain cycle validation;
- descendant membership for parent-candidate exclusion;
- deterministic connected-tree assembly;
- stable node ordering; and
- an expansion-independent hierarchy result consumed by LiveView and API representation.

The module does not own UI expansion state, form state, API serialization, or CLI rendering.
`Taskman.Tasks` remains the public facade and transaction coordinator.

The hierarchy projection contains the selected Task ID and one nested root node. Each node contains
the Task plus ordered children. Siblings use existing Task order: ascending `inserted_at`, then
ascending `id`.

### Transactional mutation

An update with no parent change follows the existing update path.

Creation with a parent and updates that explicitly set or clear a parent use one database
transaction:

1. Lock the owning Project row to serialize hierarchy mutations within that Project.
2. Reload the Task and resolved parent where applicable under the route's Project scope.
3. Reject a missing or foreign Task as not found.
4. Reject self-parenting.
5. For an existing Task, recursively walk the proposed parent's ancestor chain until reaching a
   root or the Task, rejecting the change when the Task is encountered.
6. Validate ordinary Task fields.
7. Persist ordinary fields and `parent_task_id` atomically.

Project-row locking prevents two concurrent reparent operations from each validating against a
temporarily acyclic graph and jointly creating a cycle. Mutations in different Projects do not
block one another.

Setting the already-current parent, including clearing an already-empty parent, is idempotent and
returns success.

## Browser design

### Shared parent picker

Create and edit forms compose one project-owned parent-picker component. A focused LiveView state
module owns its search query, candidate results, selected parent, open/closed state, and field-level
error.

The picker:

- uses an accessible combobox/listbox interaction;
- returns the first 20 eligible Tasks in stable order for an empty query;
- searches case-insensitively by Task title for a non-empty query;
- puts an eligible exact Task ID match first when the query is a positive integer;
- searches all Tasks in the selected Project, regardless of List ownership;
- shows title, Task ID, and full owning List path, using `Project` for a root Task;
- presents at most 20 results, with non-exact matches in stable Task order;
- supports keyboard focus and selection;
- exposes No parent in edit mode and whenever create has a selected parent; and
- keeps the selected parent visible independently of the current result page.

For existing Task editing, candidates exclude:

- the Task itself; and
- every descendant of that Task.

For creation, every existing Task in the Project is a structurally valid candidate. Search results
are advisory: the context revalidates the selected parent at save time.

Required stable DOM contracts include:

- `#task-parent-picker`;
- `#task-parent-search`;
- `#task-parent-results`;
- `#task-parent-option-<task_id>`;
- `#task-parent-clear`;
- `#task-parent-error`; and
- `#add-subtask-<task_id>`.

The implementation may add more stable IDs as needed. All key interactive elements require unique
IDs and accessible names.

### Create flow

Normal New Task routing continues to derive the creation location from the selected Project/List.
The create form initializes an empty parent picker.

Add subtask initializes separate creation state without changing the background browse route. The
state contains:

- the resolved source parent;
- the source parent's current List, or Project root when `list_id` is `nil`; and
- the ordinary new-Task form.

Submission revalidates both the creation location and parent under the selected Project. A stale or
foreign source closes no data boundary and produces a recoverable not-found result. A validation
error preserves the form, parent selection, and location copy.

After success, the create modal closes back to the preserved browse route and refreshes the Task
stream. The child appears only if its owning location belongs in the current direct or
descendant-inclusive view.

### Edit flow

Task detail initializes the parent picker from `selected_task.parent_task_id`. Parent selection and
clearing participate in the existing autosave experience but use a dedicated LiveView event rather
than masquerading as an ordinary `<select>` field.

On success, LiveView refreshes:

- the selected Task;
- the parent picker;
- the hierarchy projection;
- hierarchy-content metadata; and
- any affected streamed Task row.

On parent validation failure, LiveView closes the listbox, retains the rejected parent as unsaved
draft selection, focuses the combobox, sets `aria-invalid="true"`, links the error with
`aria-describedby`, and renders a helpful message below the input. The persisted Task, hierarchy,
and ordinary form state remain unchanged. Reopening the picker retains the draft and error; changing
or clearing the draft selection clears the stale error while the user retries.

A parent mutation and ordinary fields delivered in one API request remain atomic; browser
parent-picker events may save independently from already completed scalar autosaves, matching the
existing per-field autosave model.

### Hierarchy tree

The existing `TaskmanWeb.TaskDetail` hierarchy region is populated from the hierarchy projection.
It remains a semantic tree:

- the container uses `role="tree"`;
- each Task uses `role="treeitem"`;
- nested children use grouped lists;
- nodes with hidden children have an accessible disclosure button;
- the selected Task uses `aria-current="true"`; and
- Task-title links use canonical LiveView patches.

Required stable DOM contracts include:

- `#task-hierarchy`;
- `#task-hierarchy-toggle`;
- `#task-hierarchy-node-<task_id>`;
- `#task-hierarchy-disclosure-<task_id>` for expandable nodes; and
- `#task-hierarchy-link-<task_id>`.

The existing hierarchy shell remains initially collapsed for a Task with no parent or children
unless the user has set a session preference. A Task with hierarchy content initially opens the
shell unless the session preference says otherwise. The current responsive pushed/overlay layout,
Escape ordering, independent hierarchy scrolling, and narrow-screen behavior remain unchanged.

A disconnected Task renders as the only root node and retains the truthful
`No parent or child Tasks` message. A connected Task does not render that empty state.

## API contract

### Task create and update

Task creation accepts optional `parent_task_id` alongside the existing fields:

```json
{
  "task": {
    "title": "Implement parser",
    "list_id": 11,
    "parent_task_id": 42
  }
}
```

An omitted or `null` creation value creates a Task without a parent.

Task update accepts `parent_task_id` as an editable field:

```json
{
  "task": {
    "parent_task_id": 42
  }
}
```

JSON `null` removes the current parent. Omission leaves parentage unchanged. A request may combine
ordinary fields and `parent_task_id`; all changes in that request succeed or fail atomically.

The existing `PATCH /api/v1/projects/:project_id/tasks/:task_id` route owns this behavior. No
dedicated parent endpoint is added.

### Hierarchy query

The API adds:

```text
GET /api/v1/projects/:project_id/tasks/:task_id/hierarchy
```

The response uses the standard data envelope:

```json
{
  "data": {
    "selected_task_id": 51,
    "root": {
      "task": {
        "id": 42,
        "project_id": 7,
        "list_id": 11,
        "parent_task_id": null,
        "title": "Build import",
        "description": "",
        "status": "in_progress",
        "priority": "high",
        "due_at": null,
        "location": {
          "kind": "list",
          "list_id": 11,
          "path": ["Delivery"]
        }
      },
      "children": [
        {
          "task": {
            "id": 51,
            "project_id": 7,
            "list_id": 11,
            "parent_task_id": 42,
            "title": "Implement parser",
            "description": "",
            "status": "pending",
            "priority": "none",
            "due_at": null,
            "location": {
              "kind": "list",
              "list_id": 11,
              "path": ["Delivery"]
            }
          },
          "children": []
        }
      ]
    }
  }
}
```

Every nested `task` uses the normal Task representation. A disconnected Task is both selected and
root and has an empty `children` array. Child order is deterministic.

### Task representations

Every Task representation gains `parent_task_id`, including:

- Task list members;
- Task show;
- create;
- update;
- move; and
- nested hierarchy nodes.

The field is either a positive integer or `null`.

### API errors

Existing error envelopes and status conventions remain authoritative:

| Condition | HTTP status | Error code and detail |
| --- | --- | --- |
| Malformed `parent_task_id` or malformed hierarchy route ID | `400` | `invalid_request`. |
| Missing, stale, or cross-Project Task/parent | `404` | `not_found`. |
| Self-parent or resulting cycle | `422` | `validation_failed` with `fields.parent_task_id`. |
| Ordinary Task validation failure | `422` | Existing `validation_failed` field map. |
| Unexpected server failure | `500` | Existing `internal_error`. |

The server does not reveal that a candidate ID belongs to another Project. Repeating an existing
parent value is a successful `200 OK`, not a conflict.

## CLI contract

### Commands and options

Task creation gains:

```text
taskman tasks create --project PROJECT_ID --title TITLE [--parent TASK_ID] ...
```

Task update gains:

```text
taskman tasks update --project PROJECT_ID TASK_ID
  [--parent PARENT_TASK_ID | --no-parent] ...
```

`--parent` and `--no-parent` are mutually exclusive. Either satisfies the update command's
at-least-one-change constraint. `--no-parent` sends JSON `null`.

Hierarchy inspection is:

```text
taskman tasks hierarchy --project PROJECT_ID TASK_ID
```

There is no `tasks reparent` command. Parent changes use `tasks update`.

### Output

Structured JSON preserves the API data envelope exactly. Task objects in JSON require
`parent_task_id`.

Readable Task collection output adds a `PARENT` column containing the parent Task ID or an em dash.
Readable Task-member output includes `PARENT TASK ID`.

Readable hierarchy output prints an indented tree in API order. The selected Task is marked
unambiguously without changing its title, for example:

```text
42  Build import
└─ 51  Implement parser  [selected]
```

Rendering must remain deterministic and usable without terminal color.

### Registry and distribution parity

The shared command registry, parser, handler, help renderer, Bash/Fish completion generation,
client response validation, readable output, JSON output, onboarding, and bundled `taskman-cli`
skill all recognize the new field and hierarchy command. Onboarding adds one parent-update example
and one hierarchy-inspection example without replacing the existing basic workflow.

The bundled skill states:

- parent IDs must be exact and Project-scoped;
- inspect Tasks before changing parentage;
- `tasks update --parent` sets a parent;
- `tasks update --no-parent` clears it; and
- `tasks hierarchy` inspects the connected hierarchy.

Agent actions remain evidence rather than authorization for Task lifecycle state. Parent changes
are authorized only by the user's request and do not imply lifecycle changes.

## Failure and recovery behavior

- A candidate that becomes stale after search is rejected at persistence time.
- A concurrent hierarchy mutation is validated after acquiring the Project lock.
- Failed parent validation does not partially persist other fields from the same context/API call.
- Browser validation keeps the form and picker recoverable.
- A hierarchy URL for a missing Task uses the existing recoverable Task-not-found modal behavior.
- A hierarchy link to a Task outside the visible Task stream still opens normally because Task
  lookup is Project-scoped rather than stream-scoped.
- A database constraint failure is mapped back to `parent_task_id` where it represents a known
  hierarchy validation condition.
- No failure automatically changes Task lifecycle, List ownership, or another relationship.

## Expected file boundaries

Expected changes are limited to these responsibilities:

- a generated migration under `priv/repo/migrations/`;
- `Taskman.Tasks.Task` for the association and constraint mappings;
- `Taskman.Tasks` for public operations and transaction coordination;
- focused modules under `Taskman.Tasks` for hierarchy projection/query behavior;
- focused LiveView state and HEEx components for the parent picker;
- `TaskmanWeb.TaskForm`, `TaskmanWeb.TaskDetail`, and Task-row components for composition;
- `TaskmanWeb.ProjectLive` and its template for route and event coordination;
- API router, Task controller, representation, fallback mapping where needed, and focused tests;
- CLI registry, Task handler, output, client response validation, help/completion/onboarding
  expectations, bundled skill, and focused tests; and
- fixtures and test helpers needed to express hierarchies clearly.

The implementation plan may refine exact module names, but it must preserve these responsibility
boundaries. It must not move persistence into `TaskmanWeb` or turn `ProjectLive` into the owner of
hierarchy rules.

## Testing strategy

### Migration and database constraints

Verify:

- `parent_task_id` is nullable;
- a same-Project parent persists;
- a cross-Project composite foreign key fails;
- self-parenting fails;
- the composite-key and parent-lookup indexes exist; and
- deletion remains restricted rather than implicitly cascading or nullifying.

### Context and hierarchy

Verify:

- creation without and with a parent;
- normal update leaves an omitted parent unchanged;
- setting, replacing, and clearing a parent;
- idempotent repeated set/clear;
- same-Project enforcement;
- direct self-parent and deep cycle rejection;
- cycle rejection after several reparent operations;
- List ownership remains unchanged by parent changes;
- a child may occupy a different List from its parent;
- mixed ordinary-field and parent updates are atomic;
- two concurrent opposite reparent attempts cannot produce a cycle;
- stable candidate search and exclusions;
- exact-ID candidate lookup;
- disconnected, shallow, and deep connected trees;
- topmost-root discovery;
- deterministic sibling order; and
- full connected-tree projection.

### Components and LiveView

Use stable DOM IDs and LiveView selectors to verify:

- normal create includes an empty optional parent picker;
- Add subtask opens normal create with the source parent selected;
- the shortcut uses the source parent's List/top-level location;
- changing or clearing the selected parent does not silently change that location default;
- parent search spans the Project and disambiguates by path and ID;
- edit initializes the current parent;
- selecting and clearing autosaves successfully;
- invalid/stale choices close the listbox, retain the rejected draft selection, focus and mark the
  combobox invalid, and show a corresponding helpful message below it;
- reopening an invalid picker preserves its draft and error until the selection changes;
- hierarchy empty state remains truthful;
- ancestor path and selected Task start expanded;
- sibling branches and direct children are visible;
- other branches expand and collapse;
- manual branch expansion survives hierarchy-link navigation within one connected tree;
- closing the modal or opening another connected tree resets manual branch expansion;
- current Task highlighting and disclosure accessibility;
- hierarchy navigation preserves Project/List and descendant-inclusion context;
- navigation succeeds when the target row is absent from the stream;
- hierarchy panel preference and responsive overlay behavior remain intact; and
- stream refreshes reflect relevant persisted changes.

Tests assert outcomes and structural accessibility contracts, not styling details.

### API and CLI

Verify:

- Task representations always include `parent_task_id`;
- create/update omitted, positive, and null semantics;
- combined update atomicity;
- hierarchy endpoint shape and ordering;
- invalid request, not-found, and validation error envelopes;
- CLI parser constraints for `--parent` and `--no-parent`;
- CLI hierarchy request path and response validation;
- readable Task and hierarchy output;
- deterministic JSON passthrough;
- top-level/group/leaf help;
- Bash and Fish completions;
- onboarding and bundled skill guidance;
- malformed server responses fail as `invalid_response`; and
- real loopback HTTP end-to-end behavior.

### Completion gates

Before completion:

1. Run focused migration, context, component, LiveView, API, and CLI tests.
2. Run `mix precommit` and fix all failures.
3. Search production code, tests, help, completions, and the bundled skill for leaked planning
   terminology and parity omissions.
4. Perform wide and narrow responsive browser acceptance for create, edit, expansion, navigation,
   keyboard operation, and error recovery.
5. Obtain independent implementation verification against this specification.
6. Record verification evidence and unresolved uncertainty in the canonical delivery artifacts.

## Rejected alternatives

### Dedicated parent-relationship table

A separate table would represent a single optional parent with extra uniqueness constraints and
joins. It provides no current benefit over a nullable self-reference.

### Generalized relationship table

One table for parent-child, Blocks, and Relates to was rejected as premature. Their invariants are
materially different and would force type-specific constraints before the later types are
designed.

### Create-only parent selection

Allowing a parent at creation but not during edit creates an artificial lifecycle discrepancy and
prevents correction or reorganization.

### Row-only parent editing and dedicated parent endpoint

A Change parent row popover plus a parent API subresource would duplicate the normal Task-editing
contract. Parentage belongs in create and edit forms and in ordinary Task create/update payloads.

### Relationship editing inside the hierarchy

The hierarchy is kept focused on context and navigation. Embedding creation, reparenting, or removal
controls in each tree node would crowd the narrow sidebar and duplicate the Task form.

### Switching browse location during hierarchy navigation

Moving the workspace to each target Task's owning List would discard the user's current list
context and make modal navigation unexpectedly change the background.

### Rendering only immediate family

Parent, current Task, and direct children alone would hide broader work-breakdown context and make
sibling/cousin navigation impossible.

### Expanding the complete tree by default

Opening every descendant branch would make large hierarchies noisy. Forced ancestor/selected
expansion plus manual disclosure preserves context while controlling density.

### Coupling List ownership to parentage

Automatically moving Tasks whenever parentage changes was rejected because product semantics make
List ownership and work-breakdown hierarchy independent. Only Add subtask chooses the source
parent's location as an initial creation default.

## Implementation-planning checklist

A fresh planning session should:

1. Read this complete specification and the authoritative product relationship rules.
2. Inspect the current Task context, Task schema, autosave state, Task form/detail components,
   ProjectLive route helpers, API Task controller/representation, CLI registry/handler/output/client,
   and their focused tests.
3. Generate the migration with `mix ecto.gen.migration`.
4. Decompose implementation into repository-local Beads with explicit dependencies and acceptance
   evidence.
5. Preserve UI/API/CLI/help/completion/skill parity in each operation-bearing task.
6. Separate implementation from independent verification.
7. Finish the plan and design approval phase with an updated handoff and a fresh implementation
   session.
