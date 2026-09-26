# Active Project Selector Design

**Status:** Implemented, independently verified, and operator accepted
**Date:** 2026-09-22
**Updated:** 2026-09-26

## Context

At the design baseline, Taskman's workspace sidebar began with a static product header labelled
**Taskman** and **Projects, Lists, and Tasks**. Below it, one navigation tree interleaved every
Project, represented by a folder icon, with that Project's nested Lists. This made the static
product header look like the current Project while the actual Projects could look like folders
containing Lists.

Users rarely need to view Lists from several Projects at once. The sidebar should instead establish
one active Project and show only that Project's Lists. A Project URL identifies the active Project
for that page. When the user opens `/`, the browser should restore its last selected Project if it
still exists.

Projects persisted only `name`. Project creation was a name-only inline form at the bottom of the
sidebar, there was no Project editing workflow, and the public API and CLI could list, inspect,
and create Projects but could not update them. The navigation tree used its Project nodes to
expose root-List creation, so removing those nodes requires a replacement root action.

The implementation baseline is `origin/main` commit
`2fd2c51bb251c38d26d5d79c77d906b422fe9e09`. It includes the completed Missing List recovery and
Projects-without-primary-directories workstreams. Project routes now have the accepted recovery
behavior, and Project data is machine-independent with no local directory field.

## Outcome

The sidebar header becomes an active-Project selector. It presents the selected Project's icon,
color, name, and optional one-line description and opens a single-purpose dropdown for switching
Projects. A dedicated **Project tasks** row navigates to the Project's root Task view and hosts
root-List creation, followed directly by the selected Project's Lists. List icons distinguish
leaves, child-only Lists, and Lists with both children and direct Tasks; non-leaf disclosure shares
the icon's leading slot.

Projects gain persisted identity metadata and one unified modal supports both Project creation and
editing. Browser, API, CLI, completion, and bundled agent-skill surfaces expose the same meaningful
Project operations.

Success means:

- the selected Project is visually unambiguous and no longer appears as a folder in the List tree;
- switching Projects is explicit, URL-backed, accessible, and does not lose valid pending Task
  edits;
- the last selected Project is restored from this browser when the user opens `/` again;
- descendant inclusion and status selection survive navigation between Project roots and Lists
  without being copied into navigation URLs;
- a dedicated Share control copies the current route with both Task-table filters encoded for
  another browser;
- List icons communicate structure and direct Task ownership without a separate chevron column;
- the Project root Task view remains directly reachable from any selected List;
- the navigation below the selector shows only the active Project's root view and Lists;
- Project creation and editing use one coherent modal and validation contract;
- existing Projects receive safe deterministic metadata;
- Project metadata is consistent across browser, API, CLI, and agent-facing surfaces; and
- focused automated coverage, responsive browser review, and `mix precommit` pass.

## Scope

### Included

- Persisted Project description, icon, and color.
- Browser-local persistence of the last selected Project ID.
- Browser-local persistence of descendant inclusion, aligned with the existing stored status filter.
- An icon-only Share button and explicit filter-query inputs for shared URLs.
- A header-based active-Project selector and Project-switching dropdown.
- A dedicated Project-root Task row with root-List creation above the List tree.
- A Lists-only navigation tree scoped to the active Project.
- List icon classification and combined icon/disclosure behavior.
- One modal for Project creation and editing.
- Project update behavior in the context, API, and CLI.
- Project metadata in API representations, CLI output, completions, and the bundled skill.
- Reconciliation of Project metadata changes in a mounted workspace.
- Responsive, keyboard-accessible behavior and focused browser acceptance.

### Excluded

- Project deletion or deletion safeguards.
- Project reordering, pinning, grouping, or search.
- Server-side or account-wide selected-Project preferences, or automatic first-Project selection.
- Custom or uploaded Project icons.
- Machine-local checkout metadata or agent execution context. Projects have no directory, and this
  design does not reintroduce one.
- Persisting selector-open, modal-open, or List-expansion presentation state.

## Decisions

### Project URLs take precedence over the browser's remembered selection

Routes containing `:project_id` select that Project and update the browser's remembered Project ID
when the Project exists. A direct Project or List URL always takes precedence over any previously
remembered ID. The browser stores the decimal Project ID under
`taskman:selected-project-id:v1` in origin-scoped `localStorage`; it does not store List selection,
Task route, Project metadata, or a server-side preference.

On `/`, after the LiveView connects and the initial Task-table preference snapshot has been
hydrated, the browser reads the remembered ID. An absent value requires no server event; a
malformed value is removed locally. A syntactically valid ID is sent through
`restore_remembered_project` once per visit to `/`. The server handles that event only while the
current action is `:index`, preference hydration is complete, and no recovery flow is active. It
revalidates the Project ID and, if found, patches to `/projects/:project_id` with history
replacement. The event reply distinguishes accepted, stale, and ignored requests. The hook removes
a stale value only if storage still holds
the ID it submitted, so a later valid selection is not cleared by an older reply. An ignored reply
does not change storage. This restores the Project's root Task view without adding a
redundant `/` entry to browser Back history. It never selects the first Project by default. If
storage is empty, unavailable, malformed, or refers to a missing Project, `/` shows the neutral
**Choose a Project** state. Restoring the Project changes only the selected location; it does not
reset **Include child Lists** or **Statuses**. On a fresh load of `/`, each filter is restored from
its browser value unless that filter has an explicit query parameter on `/`, in which case the
parameter wins. Project restoration waits for the LiveView to acknowledge that both filter values
were applied; its replacement patch to the Project root then omits both filter parameters while
keeping their resolved values in the mounted LiveView. A failed storage read still completes
hydration with defaults or explicit parameters, so it cannot indefinitely block Project selection.

The application navbar's **Taskman** link remains a fixed link to `/`. The navbar does not read
browser storage or receive the active Project as a destination. Clicking it from a Project or List
route enters `/`, where the same restore flow validates the remembered ID and patches to that
Project's root view without changing either filter. If there is no usable remembered ID, the
neutral state remains visible, with both filter preferences still hydrated for a later Project
selection.

An invalid explicit Project URL retains the existing Project-not-found state and does not restore
another Project over that URL or replace the remembered valid ID. A valid Project with a missing
List URL still counts as selecting that Project for browser memory, while the existing List
recovery state remains visible. Opening `/` again restores that Project at its root.

Selecting a Project from the dropdown navigates to `/projects/:project_id`. It intentionally
selects that Project's root rather than guessing a previously visited List. Existing Project/List
and Task-detail routes remain canonical and shareable.

The sidebar also provides a **Project tasks** link to `/projects/:project_id` whenever a valid
Project is active. It is the direct route from a List back to the Project's root Task view. The
link does not change the active Project or open the selector. The Project root continues to show
direct Project Tasks when descendant inclusion is off; its existing **Include child Lists** control
can include List Tasks. Returning to the root through this link clears the List selection but
preserves the browser's descendant-inclusion and status preferences.

### Task-table filters are browser preferences with explicit share snapshots

**Include child Lists** is one origin-scoped browser preference shared by all Project and List
views, not a property of an individual Project or location. The browser stores `"true"` or
`"false"` under `taskman.task-table.include-children` in `localStorage`. With no stored value, the
default is false. Toggling the control updates the current LiveView's inclusion state and Task
stream immediately and writes the preference without navigating or patching the URL. Moving
between the Project root, Lists, Projects, and Task routes retains that state in a mounted
LiveView. A fresh mount reads the browser preference and restores it before treating the Task
table as ready. Malformed stored values are discarded and fall back to false. If storage is
unavailable, the toggle still works for the mounted LiveView; a fresh mount uses the default.

The **Statuses** filter already persists its selected statuses in browser `localStorage` under
`taskman.task-table.visible-statuses`. This design retains that existing global, browser-scoped
contract and its default of all statuses except `will_not_do`. It must remain selected across the
same Project, List, root, and Task-route transitions; it is neither reset by Project switching nor
copied into ordinary navigation URLs. Sorting and the open/closed state of the filter menu remain
transient.

All newly generated workspace navigation destinations omit both `include_children` and `statuses`:
the Project selector, **Project tasks** link, List tree, Task creation/detail links, return/cancel
paths, recovery redirects, and Project-selection or `/` restoration patches. An explicit filter
parameter on `/` is accepted before Project restoration and persists through the parameter-free
Project-root patch; the other filter still comes from mounted/browser state or its default. The
route handler treats an absent parameter as “keep the mounted value,” not as a reset. Whenever a
Project, List, Task-creation, or Task-detail URL with an explicit filter parameter becomes
active—including an initial load and Back or Forward history traversal—that parameter overrides
the corresponding mounted and stored browser preference and updates browser storage. The two
parameters act independently. `include_children=true` enables inclusion; any other supplied
string disables it, preserving the previous query contract. `statuses` is one comma-separated string of Task status
keys in any order; for example, `statuses=done,pending,in_progress`. An empty `statuses=` selects
no statuses. Unknown keys are ignored and duplicates are removed; order has no filtering meaning.
The parsed selection is normalized to `Task.statuses()` order for stable UI rendering without
creating atoms from input. A malformed non-string parameter is ignored as if absent. On a fresh
mount, missing parameters use their respective stored values or defaults; on a mounted route
change, they retain the current values. Explicit parameters remain in the address bar when
accepted; accepting them does not strip or canonicalize the URL. Ordinary navigation destinations
omit them and retain the mounted preferences. Leaving a parameter-bearing URL does not rewrite
its history entry: returning to it with Back or Forward reapplies its filter snapshot immediately,
and reloading it does the same.

If a filter key appears more than once in the query string, that filter is ignored as if absent;
the other filter still applies if its key appears exactly once. Browser parsing and server route
handling use the same rule so repeated keys cannot produce different visible Tasks or Share links.
Generated Share URLs contain exactly one key for each filter.

Changing either **Include child Lists** or **Statuses** persists the new preference and removes
both filter parameters from the current URL by replacing that history entry, without navigating,
creating another history entry, or dropping unrelated query parameters or the fragment. A refresh
of the changed entry therefore uses the updated browser preferences rather than reapplying the
old snapshot. The cleanup applies to the entry being changed, not to an earlier shared-link entry
the user has already left. This keeps browser history truthful: Back to an untouched shared URL
restores its original filter choices; Back to an entry whose filters were changed does not.

An icon-only **Share** button sits immediately left of the **Include child Lists** switch in the
Task-table toolbar, only when a valid Project Task view is available. It has a stable DOM ID,
accessible label, visible focus state, and success/failure feedback announced to assistive
technology. Clicking it does not navigate or modify the current address bar. It copies an
absolute URL made from the current route, preserving unrelated query parameters and any fragment,
while setting exactly one `include_children=true|false` and exactly one `statuses=` parameter from
the current LiveView filter state. Both parameters are included even at their defaults; an empty
status selection is represented by `statuses=`. The Share button serializes selected status keys
in `Task.statuses()` order for a stable URL, not because their order affects filtering. Existing
filter parameters in the current URL are replaced in the copied URL, so a shared link never
captures stale values after the user changes either filter. The button is not usable until initial
browser-preference hydration is complete. Clipboard failure leaves the current page unchanged and
exposes the generated URL for manual copying. The Task-detail modal has its own square Share
button immediately left of **Move Task**, matching that button's height. Its link targets the
selected Task detail route even if the address bar has not caught up with the modal. After a
successful copy, a short-lived message replaces the persistent popover; clipboard failure keeps
the manual-copy field available. Share becomes usable again after LiveView reconnects and
rehydrates browser preferences. The toolbar control is labelled **Share current view** and the
Task-detail control **Share Task**.

A copied plain navigation URL still identifies the same Project, List, or Task but uses the
recipient's own filter preferences; the Share button is the deliberate way to reproduce the
sender's current filters. Back and Forward to clean URLs retain the mounted choices; traversing
to a filter-bearing URL reapplies its explicit snapshot. This replaces the earlier automatic
URL-backed descendant-filter contract from the Lists design while retaining explicit-query
compatibility.

### The selector dropdown only switches Projects

The expanded dropdown contains Project choices and no create or edit actions. This keeps one clear
purpose: changing the active Project.

The collapsed selector has a separate icon-only edit button immediately to the left of its
disclosure button. The edit button exists only when a Project is selected. A full-width primary
**New Project** button at the bottom of the sidebar replaces the current inline creation form.

### Creation and editing share one modal

One Project modal renders the same fields and validation for create and edit modes:

- name;
- description;
- icon;
- color.

Creation starts with the standard icon and color defaults. Successful creation closes the modal
and navigates to the new Project root. Successful editing closes the modal and preserves the
current Project, List, browser-held Task-table preferences, and Task-detail route.

Color has one always-visible text control with a static `#` prefix and an editable six-character
hex field, with curated rounded-square preset swatches above it. Only exactly six hexadecimal
digits form a valid editable value; incomplete or non-hex text cannot be saved. The form maps the
six editable digits to and from the canonical `#RRGGBB` Project color. Selecting a preset fills
those six digits; typing a valid arbitrary color uses that value without a separate custom mode.
A preset shows selected styling only when its color matches the current valid input, and its name
and selected state are accessible without relying on color alone. Invalid input displays a
field-level error. Editing loads the stored color into the field even when it is not a preset, so
unrelated edits preserve it.

The static prefix uses [daisyUI's text-inside-input composition](https://daisyui.com/components/input/#text-input-with-text-label-inside).
Taskman's project-owned `<.input>` originally rendered a plain text input; it supports this
prefix while retaining its form-field binding, label, and error behavior. The shared Project
changeset still validates the canonical full color value.

### Project identity is stored on the Project

Identity metadata belongs directly to each Project. A separate preferences table would add
lifecycle and query complexity without a distinct owner or consumer. Named theme tokens would
couple persisted data to the preset swatch list and complicate arbitrary-color editing.

The database stores the actual validated color and a stable icon key. The Project create/edit modal
offers curated icon options and preset color shortcuts alongside the arbitrary-color text input.

## Persistence model and validation

The `projects` table gains these columns:

| Column | Contract |
| --- | --- |
| `description` | Non-null string, default `""`; trimmed at both ends; maximum 160 characters. |
| `icon` | Non-null string, default `"briefcase"`; one allowed application-owned icon key. |
| `color` | Non-null string, default `"#6366F1"`; normalized uppercase `#RRGGBB`. |

The migration uses these defaults to backfill existing Projects and retains the defaults as a
database safety boundary. The Ecto schema declares matching defaults so new forms and ordinary
application-created Projects agree with persisted behavior.

Allowed icon keys are:

- `check-circle`;
- `folder`;
- `briefcase`;
- `code-bracket`;
- `rocket-launch`;
- `beaker`;
- `light-bulb`; and
- `wrench-screwdriver`.

They map to the existing project-owned `<.icon>` component by adding the `hero-` prefix. Arbitrary
icon strings are rejected before rendering.

`briefcase` is the default so an uncustomized Project reads as a Project rather than a completed
Task (`check-circle`) or a List container (`folder`). Those icons remain available as explicit
choices.

`color` accepts a leading `#` followed by exactly six hexadecimal digits. Input is normalized to
uppercase before validation. The preset swatches use vetted indigo, blue, cyan, emerald, amber,
orange, rose, and fuchsia values. Rendering derives a light or dark icon foreground from the stored
color's relative luminance, so API-created valid colors do not make the glyph unreadable.

`description` may be empty but is never `NULL`. Empty description input is normalized to `""`.
The existing trimmed, non-empty name rule remains unchanged.

## Context contract

`Taskman.Projects` retains its existing list, lookup, create, and changeset functions and adds:

- `update_project(project, attrs)` returning `{:ok, project}` or `{:error, changeset}`.

The function revalidates every supplied field through the shared Project changeset. Ownership is
not user-scoped in the MVP, and there is no update-by-untrusted-ID shortcut in the context.
If the Project row disappears before either a no-op or changed update, the function returns
`{:error, changeset}` and publishes no notification, preserving the context's error contract.

Creation publishes `:created` with all materially initialized Project fields. Updating publishes
`:updated` with the exact persisted fields that changed. `Taskman.ChangeNotifications` therefore
expands its Project operation contract from create-only to `:created | :updated` and includes the
new metadata fields in deterministic field ordering. A valid no-op update returns the canonical
Project without publishing a misleading change notification.

`Taskman.Tasks.list_ids_with_direct_tasks(project)` returns a `MapSet` of List IDs owning at least
one direct Task in that Project. It ignores Task status and descendant-inclusion preferences,
returns no Task records or counts, and lets the navigation builder classify Lists without per-node
queries. The web layer obtains this through the context, not a direct repository query.

Project notification reconciliation refreshes the Project stream used by the selector. When the
changed Project is selected, it also replaces `workspace.selected_project` with the canonical
record without changing the selected List, route, Task listing, task drafts, movement state, or
Project subscription. The merged reconciliation boundary must accept well-formed Project
`:updated` events in addition to `:created` events; Missing List recovery behavior and recovery
state remain otherwise unchanged.

## Sidebar behavior

### Viewport layout and narrow navigation

The authenticated workspace fills the viewport below the application navbar. Its outer content
does not scroll. At widths of at least 1024 CSS px, the Project sidebar and Task panel sit side by
side. The Project selector and Project tasks row stay above a separately scrollable List tree, while
**New Project** stays visible at the bottom. The main heading and controls stay above the scrollable
Task list; its column header remains visible while Task rows scroll.

Below 1024 CSS px, the Task panel uses the full workspace width and the Project sidebar opens as a
drawer from a **Projects** button. The backdrop, close button, and Escape dismiss the drawer;
choosing a Project or List also closes it. Focus moves into the opened drawer and returns
to its trigger on dismissal. Main controls wrap within the narrow panel. Hiding the List tree until
the drawer opens gives the Task list the available viewport height instead of splitting it with a
stacked sidebar.

### Selected Project header

The selector keeps the pre-change static **Taskman** sidebar header's placement and visual hierarchy:
a compact horizontal row with a size-10 rounded icon tile with its shadow, and the same
primary-name and muted-secondary-text typography. The tile and text show the selected
Project's identity instead of static application branding. The only added header controls are the
compact edit and disclosure buttons at the right; do not redesign the header as a separate card.

The selected state contains:

- a rounded icon tile using the Project color and derived foreground;
- the Project name as the primary line;
- the Project description as an optional secondary line;
- an icon-only edit button; and
- a disclosure button whose chevron points down when collapsed and up when expanded.

The text region takes the space remaining between the icon and controls and can shrink without
pushing either outside the sidebar. The name and description stay on single lines and truncate
when needed; the description uses the same one-line truncation treatment as List labels. When the
description is empty, its element is omitted and the name is vertically centered beside the icon.

The selector identity area and disclosure affordance open or close the Project choices. The edit
button is a separate control and never toggles the selector. Buttons have distinct accessible
labels, visible focus states, and stable DOM IDs. The edit and disclosure buttons sit 4 px apart.

Icon-only controls in the workspace and its dialogs show a compact tooltip after a short hover
delay, or immediately on keyboard focus. Tooltips stay within the viewport above scrollable panes;
accessible labels retain context even when the visible tooltip uses a shorter action name.

### Neutral state

Without a selected or restorable Project, the header shows a neutral icon and **Choose a Project**
label. It has no description and no edit button. Its disclosure still opens the available Project
choices. If there are no Projects, the dropdown renders a concise empty state and the bottom **New
Project** button remains the creation path. Because the server cannot read browser storage during
the initial render, `/` may briefly show this neutral state before a remembered Project is
restored after connection.

### Project choices

The dropdown is labelled as Project navigation and contains URL-backed Project links. Each choice
shows the same icon tile, name, and optional one-line truncated description contract as the header.
The current Project is identified with `aria-current="page"` and selected styling.

The dropdown is viewport-safe, scrolls when its content exceeds its maximum height, closes after a
selection, and dismisses on outside click or Escape. Native links and buttons retain ordinary Tab,
Enter, and Space behavior; no bespoke JavaScript selection model is introduced.
Project choices have a small vertical gap without widening the dropdown beyond its container.

### Project root and Lists-only navigation

A distinct **Project tasks** row starts the navigation, followed directly by the active Project's
root Lists with no separate **Lists** heading. The row remains visible when the Project has no
Lists. Its link has `aria-current="page"` and selected styling when the current location is the
Project root, including a root-context Task detail or creation route. A missing List route does
not mark this link selected. When no valid Project is active, the row and List tree are absent.

An icon-only **Add root List** button sits at the trailing edge of the **Project tasks** row,
separate from its navigation link. Activating the button does not navigate; it opens an anchored
form from that row through the existing `ListEdit` workflow. This replaces root-List creation on
the removed Project tree node. The **Project tasks** row is not an expandable parent of the root
Lists.

The navigation region renders only Lists belonging to the selected Project. Root Lists use
`aria-level="1"`; children increment from there. List selection, ancestor-expansion behavior,
nested List creation, renaming, stable ordering, and session-scoped expansion-state semantics retain
their existing behavior. List iconography and the manual expand/collapse affordance change as
specified below. Each List row places **Rename List** before **Add child List**; those short tooltip
names do not replace the List-specific accessible labels.

Each List's icon depends on whether it has child Lists and at least one Task assigned directly to
it, regardless of the active status filter or **Include child Lists** preference:

| Child Lists | Direct Tasks | Resting icon |
| --- | --- | --- |
| None | Any, including none | `hero-list-bullet` |
| One or more | None | `hero-folder` when collapsed; `hero-folder-open` when expanded |
| One or more | One or more | `hero-queue-list` in both expansion states |

This is a derived presentation state, not persisted List metadata. A child List's Tasks do not
make its parent a mixed List. The icon must reflect zero-to-nonzero and nonzero-to-zero transitions
in both direct-Task and child-List presence. This does not add Task or List deletion operations;
currently, Task creation and movement or child-List creation can cause the applicable transitions.

Non-leaf Lists use one fixed-size leading disclosure button instead of separate icon and chevron
slots. At rest it displays the icon above. When the List row is hovered, it displays
`hero-chevron-right` if collapsed or `hero-chevron-down` if expanded in that same slot. Keyboard
focus on the disclosure button also displays the chevron; on touch devices, the resting icon
remains a tappable disclosure control. Hover or focus changes only the visual, not the expansion
state. Activating the button toggles expansion and keeps keyboard focus on that disclosure;
activating the separately focusable List name navigates to the List. The button retains
`aria-expanded`, an Expand/Collapse label, a visible focus indication, and a generous hit target.
Icon-to-chevron swaps do not shift the row or text.
Leaf Lists have one decorative `hero-list-bullet` icon beside their navigation link and no
disclosure button or extra chevron placeholder. The ordinary List icon slot still aligns labels
with non-leaf rows. Existing add-child and rename actions remain independently operable.

Navigating to a List opens its ancestor path so the selected List is visible. An explicit
disclosure click may then collapse any ancestor without changing the selected location; the main
heading continues to identify that location. Navigating to a different selected List reopens the
ancestors needed to reveal it.

Switching Projects resets the visible navigation stream from one Project-scoped snapshot. Existing
session-scoped expansion identities may remain in memory; globally unique List IDs ensure they only
take effect if their owning Project becomes active again.

The Project-scoped navigation snapshot obtains the set of List IDs with direct Tasks in one
Task-context query, independent of status and descendant filters. The pure List-tree flattener
combines that set with child-List presence and expansion state to form each navigation node; it
does not query Tasks per node. Task creation and movement affecting the active Project refresh
the relevant navigation nodes, including changes delivered through workspace notifications.

## LiveView and component boundaries

`TaskmanWeb.ProjectLive.Workspace.State` gains:

- `project_selector_open?`, a transient boolean; and
- `project_edit`, a `%TaskmanWeb.ProjectLive.ProjectEdit{}` value.

`ProjectEdit` is a socket-free value modelled after `ListEdit`. It owns closed/create/edit mode,
the target Project, `to_form/2` form, modal title and submit label, validation transitions, and
stale-target reconciliation. It does not call the repository or own LiveView lifecycle.

A focused Project-selector function component renders the header and dropdown from the Project
stream and current Workspace state. `WorkspaceNavigation` becomes Lists-only and retains ownership
of the separate Project-root link, tree markup, and List forms. The Project modal is a function
component composed from the existing modal, form, input, and icon components. No LiveComponent is
introduced.

The LiveView continues to own persistence events, URL navigation, streams, subscriptions, and
cross-workflow coordination. Project switching uses normal LiveView patch navigation, so the
existing route-change logic flushes valid pending Task edits before changing context. Active
recovery overlays keep the workspace inert, including Project selector and editor controls, until
the recovery decision completes.

A focused browser hook on its own stable DOM element owns Project memory. On a valid
Project-backed route, it writes the canonical Project ID to `localStorage`; on `/`, it reads and
offers the saved ID to the restore event described above. The hook guards against repeated restore
attempts during LiveView updates, never overrides an explicit Project URL, does not write while the
route is Project-not-found, and does not persist a List ID. Storage read/write exceptions leave
navigation usable through the selector. The existing task-detail layout hook remains independent.

A focused Task-table preference hook, separate from Project-ID restoration, coordinates the two
filters. On fresh mount it resolves each filter from the URL parameter, stored value, or default in
that precedence order; sends one hydration event to the LiveView; and persists accepted explicit
URL values. The existing status-only storage hook is folded into this owner so a delayed storage
restore cannot overwrite a shared URL's `statuses`. On later route changes, explicit parameters
again override the mounted values, including on Back and Forward, while absent parameters leave
them intact; the hook persists those route overrides to browser storage. The LiveView persists
user filter changes and replaces the current URL without its filter parameters. Explicit query
values win even when Task detail or recovery is active. URL path helpers stop appending filter
parameters, while Task-detail canonical-route and recovery checks compare route identity
independently of the in-memory filters. A Share hook reads the current LiveView filter values and
uses the browser URL and clipboard APIs; it does not read stale filter parameters as its source of
truth.

The LiveView marks initial preference hydration complete only after applying both resolved values.
The separate Project-memory hook observes that state on its stable element and offers a remembered
Project ID only then. The server also rejects a premature restore event. This ordering prevents a
Project-root patch from replacing a parameter-bearing `/` URL before its filter snapshot is applied.

Expected implementation boundaries include:

- a generated migration under `priv/repo/migrations/`;
- `lib/taskman/projects/project.ex`, `lib/taskman/projects.ex`, and
  `lib/taskman/change_notifications.ex`;
- `lib/taskman/lists.ex` and `lib/taskman/lists/navigation_node.ex` for a Project-scoped,
  Lists-only flattened navigation model and derived List icon state;
- `lib/taskman/tasks.ex` for a batched Project-scoped direct-Task presence query;
- `lib/taskman_web/live/project_live/project_edit.ex`;
- `lib/taskman_web/live/project_live/workspace.ex`;
- `lib/taskman_web/live/project_live/paths.ex` and Task workflow route/recovery helpers, to remove
  descendant-query propagation without losing filter state;
- `lib/taskman_web/live/project_live/reconciliation.ex`;
- `lib/taskman_web/components/project_selector.ex`;
- `lib/taskman_web/components/core_components.ex` for the optional static input prefix;
- `lib/taskman_web/components/workspace_navigation.ex`;
- `lib/taskman_web/components/tasks/table.ex` for coordinated filter hydration and Share-adjacent
  status-filter integration;
- `lib/taskman_web/live/project_live.html.heex` for the selector and browser-hook bindings;
- `assets/js/project_live_hooks.js` for Project memory, Task-table preferences, Share, and
  narrow-screen drawer focus;
- the existing API router, Project controller, and representation modules;
- Project CLI registry, command, presentation, completion, and response-validation surfaces;
- `priv/taskman_cli_skill/SKILL.md`; and
- focused context, migration, component, LiveView, API, CLI, completion, and skill tests.

Names may be refined mechanically during planning, but responsibilities and public behavior are
fixed by this specification.

## API contract

Every Project representation contains:

```json
{
  "id": 7,
  "name": "Taskman",
  "description": "Task planning and delivery",
  "icon": "briefcase",
  "color": "#6366F1"
}
```

`POST /api/v1/projects` accepts `description`, `icon`, and `color` in the existing `project`
object. Omitting them uses the documented defaults.

`PATCH /api/v1/projects/:project_id` accepts a partial `project` object containing any editable
Project fields and returns the updated Project representation with `200 OK`. Unknown input fields
remain ignored by changeset casting. An invalid representation returns the existing
`422 validation_failed` envelope; a malformed ID returns `400 invalid_request`; a missing Project
returns `404 not_found`.

An empty valid update object is a successful no-op and returns the canonical representation. This
keeps HTTP idempotence simple; the CLI separately prevents an update invocation with no field
options.

## CLI and bundled skill contract

Project creation becomes:

```text
taskman projects create --name NAME \
  [--description TEXT] [--icon ICON] [--color '#RRGGBB']
```

Project update is:

```text
taskman projects update PROJECT_ID \
  [--name NAME] [--description TEXT] [--icon ICON] [--color '#RRGGBB']
```

At least one update option is required. Icon options use the finite allowed values. Color remains a
validated string; the CLI and browser modal both accept any valid `#RRGGBB` color.

Readable Project output adds identity metadata without obscuring the ID and name. JSON output
passes through the complete API representation. Generated Bash and Fish completions, command help,
onboarding examples where relevant, response-shape validation, and the bundled Taskman CLI skill
are updated together. No Project command accepts or emits a directory.

## Error and recovery behavior

- Invalid name, icon, color, or overlong description renders field-level modal errors and
  preserves all entered values.
- A stale Project edit target is re-fetched before submission. If absent, the modal shows a
  recoverable unavailable state and does not create or mutate a Project.
- A failed create or update keeps the modal open and leaves selector, route, Lists, and Tasks
  unchanged.
- A successful edit updates every visible representation of the Project without resetting the
  selected List or task state.
- A Project notification arriving while an edit form is pristine rebuilds it from the canonical
  Project. Once the form contains user parameters or validation errors, reconciliation refreshes
  the target identity but preserves those parameters. Submission applies the complete preserved
  form to the freshly fetched target, so the explicit local submission wins over concurrent field
  changes in this single-user workspace.
- Malformed or unsupported icon and color values never reach dynamic icon rendering or unsafe CSS
  construction.
- An absent remembered Project ID requires no action. A malformed or stale ID does not navigate;
  it is removed from browser storage when possible, leaving the neutral selector usable. Browser
  storage failure never blocks explicit Project selection.
- Existing Project-not-found and List-not-found route recovery remains unchanged.

## Documentation updates

Implementation updates the canonical domain document with Project identity metadata. This
specification owns the expanded current Project representation and update operation; the older
API/CLI design remains historical provenance with its existing supersession notice rather than
being rewritten. The documentation index changes only when an artifact or status changes. The
implemented Projects-without-primary-directories specification remains authoritative for the rule
that Project data is machine-independent and contains no directory.

## Testing and verification

### Migration and context

- Existing Projects receive the exact description, icon, and color defaults.
- All three columns reject `NULL` and retain database defaults.
- Description trimming, empty normalization, and maximum length.
- Icon allowlist and color normalization/validation.
- Create defaults and explicit metadata.
- Partial and full update behavior and no-op updates.
- Exact creation/update notification fields, Project update publication, merged reconciliation
  acceptance, and no publication on invalid or no-op changes.

### Components and LiveView

- Neutral selector state, absent edit button, and empty dropdown state.
- Selected header metadata, omitted empty description, and structural one-line truncation contract.
- Distinct edit and disclosure controls with stable IDs and accessible labels.
- Project choices, current-project semantics, selection navigation, Escape, and click-away dismissal.
- Remembering valid Project and List route selections; restoring a valid ID from `/` with history
  replacement and without repeated attempts; explicit URL precedence; malformed, stale, delayed
  replies, unavailable storage behavior, and rejection of restoration before filter hydration.
- The application navbar link remains `/`; clicking it from a List follows the ordinary restore
  path to the remembered Project root without changing either Task-table filter.
- Project-root link visibility and selected state, including root-context Task routes; List-to-root
  navigation, retained Task-table preferences, and the root link's absence on a missing Project.
- Descendant inclusion and statuses restore from browser storage on a fresh `/` load unless their
  respective URL parameters are supplied; those explicit values survive the Project-root patch.
  Both remain selected through root/List/Project navigation, Task routes, the navbar's `/` round
  trip, and reload; ordinary links and patches omit both filter parameters.
- Status selection remains browser-stored and survives the same navigation, including Project
  switching; absent, malformed, and unavailable storage have defined defaults/fallbacks.
- Explicit URL parameters independently override browser-stored filters on initial load, Back,
  Forward, and reload; an empty `statuses=` selects none, while unknown status keys are ignored
  and status order or duplicates do not change the selected set.
- Changing either filter saves its new value and removes both filter parameters from the current
  history entry without navigation or loss of unrelated URL parts. Navigating away leaves an
  untouched shared-link entry intact, so Back immediately restores its original choices; an
  edited entry stays clean on Back and reload.
- The Share control copies the current absolute route with both current filter values, replaces
  stale filter parameters without changing the address bar, preserves unrelated query and fragment,
  and handles clipboard success/failure accessibly.
- Route canonicalization, Task-detail recovery, and missing-List recovery preserve the in-memory
  descendant setting without relying on query equality.
- Lists-only tree content, root depth, a **Project tasks** row without a redundant section heading,
  a separate **Add root List** button on that row, nested expansion, rename, and strict active-Project
  isolation.
- Leaf, child-only, and mixed List icon classification, including empty leaves, independence from
  filters, both directions of direct-Task and child-List presence in the navigation model, and
  cross-session Task changes. LiveView updates cover currently available Task moves and child-List
  creation.
- One non-leaf icon/disclosure slot with collapsed/expanded and hover/focus visuals, operable
  keyboard/touch disclosure, separate List navigation, and no extra chevron space for leaves.
- Shared create/edit modal fields, accessible icon radio group, hex color input, preset swatch
  buttons, validation, successful navigation, edit route preservation, stale targets, and
  notification reconciliation.
- The static `#` prefix is not editable; the field shows only six editable characters, round-trips
  the canonical color, and rejects incomplete or non-hex values. Preset selection fills those
  characters; manual values select a matching preset only, and editing a valid non-preset color
  preserves it on unrelated submissions.
- Valid pending Task edits flush before Project switching; active recovery keeps controls inert.

Tests assert behavior and meaningful accessibility structure rather than visual spacing or
decorative class details. Low-level component coverage may inspect truncation structure where that
contract cannot be exercised through LiveView behavior.

### API, CLI, and agent surfaces

- Expanded Project representations for list, show, create, and update.
- Partial update, no-op, validation failure, malformed ID, and missing ID behavior.
- Exact CLI create/update request bodies, readable and JSON output, response validation, and exit
  statuses.
- Update-option constraints, icon values, command help, Bash/Fish completions, and bundled skill
  guidance.

### Browser and repository gates

Perform focused browser acceptance at desktop and narrow widths for selector sizing, description
truncation, dropdown scrolling and dismissal, modal operation, keyboard focus, Project switching,
browser reload and `/` restoration, direct-link precedence, Lists-only navigation, and both stored
Task-table preferences across locations. Exercise preset and custom color entry by keyboard and
pointer. Exercise Share from root, List, and Task-detail routes, including empty statuses,
previously parameterized URLs, Back/Forward restoration, filter-change URL cleanup, reload, and
clipboard failure. Check List icon/chevron swaps, keyboard focus,
touch-width targets, and absence of row shifts. Then run focused tests followed by `mix precommit`
and fix all failures.

Before completion, search implementation-facing files for leaked planning terminology and verify
that the updated documentation index and handoff references resolve.

## Accepted trade-offs

- The Project create/edit modal uses one always-visible hex input, with a static `#` prefix and
  preset swatches as shortcuts. This adds a small input to the modal but makes arbitrary valid
  colors directly editable and avoids a separate custom-radio state or special swatch for a
  previously stored non-preset color.
- The browser remembers only a Project ID for opening `/`; explicit URLs retain authority for
  direct links, and no List or Task location is restored.
- Task-table filters are browser-scoped during ordinary navigation. Plain copied URLs do not
  reproduce another browser's filters; the Share button deliberately generates a filter snapshot.
  An untouched snapshot history entry remains self-describing and reapplies on Back/Forward or
  reload. Changing a filter consumes only the current entry's snapshot by removing both filter
  parameters; it does not rewrite earlier history entries.
- List expansion state may be remembered within the mounted LiveView when switching away and back,
  but it is not persisted.
- Project identity metadata remains part of the Project record rather than a separate preference,
  favoring a small coherent model over speculative separation.
- An explicit edit submission may overwrite a concurrent edit to the same Project fields. The MVP
  is a local single-user workspace and Projects have no optimistic-lock contract; adding one is
  outside this navigation-focused change.
