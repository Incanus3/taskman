# Taskman — MVP Product Specification

**Status:** Resolved product specification

## 1. Product role

Taskman is an authenticated, hostable web application for organizing work in Projects, Lists,
and Tasks. It is the system of record for those work items and their relationships.

The core journey is: create a Project; organize work in Lists and Tasks; perform and
review the work; then explicitly mark each completed Task Done. Agent Session integration is
deferred to a separate accepted design.

## 2. Users, delivery, and persistence

- Explicitly provisioned users access one shared Taskman workspace through its browser UI or
  accompanying CLI.
- Product data persists in the Taskman server's PostgreSQL database.
- A Project is a machine-independent logical work container identified by its ID and name, with
  editable description, icon, and color. It has no directory.
- Authentication is an application-wide access gate. Projects, Lists, and Tasks are not owned or
  filtered by user, and the MVP has no collaboration permissions or Task-edit attribution. Explicit
  Task comments record the authenticated posting account as described below.
- Taskman can run as an OTP release behind an HTTPS reverse proxy on a dedicated server.

## 3. Core domain model

| Concept | MVP definition |
| --- | --- |
| **Project** | Top-level work container with an ID, name, description, icon, color, an unbounded acyclic tree of Lists, and Tasks. |
| **List** | Nested organizational container belonging to exactly one Project. It can contain Lists and Tasks. |
| **Task** | Intended work, owned by exactly one Project and one location: directly under that Project or in one List. |
| **Checklist** | Ordered, informational completion markers on a Task. |
| **Task comment** | Append-only plain-text discussion on one Task, with a posting account, optional custom display name, and creation time. |
| **Task relationship** | A Blocks / Blocked by or parent-child association; it is independent of List ownership. |

Tasks may move only between locations in their current Project. A new Task defaults to **Pending**,
and a user may explicitly select another lifecycle state during creation. Its fixed lifecycle is
**Icebox → Pending → In Progress → In Review → Done**; In Review may return to Pending or In
Progress. **Will Not Do** is the other terminal state. Explicit assignment to a nonterminal Task
authorizes bounded agent updates, evidence comments and explicit In Progress/In Review transitions.
Done, Will Not Do, reopening terminal work, blocker overrides, deletion and material scope/priority
changes require explicit operator decisions. A human reviews and accepts work before Done. See
the [agent-work contract](domain.md#agent-work); these operating rules add no API enforcement or
automatic lifecycle transitions.

After a successful Task move, the browser stays on the current Project/List backdrop if the Task is
still in its direct or enabled descendant location scope. Otherwise it follows the Task to its new
location. A row move opens that location's Task list; a detail move keeps the same Task editor open
over that location. The Include child Lists setting is preserved, and status filters do not affect
the route choice.

Task priority is required and exactly one of **None**, **Low**, **Medium**, **High**, or **Urgent**.
Description, local due date-time, and checklist are optional. Neither checklist progress, due date,
nor external agent work automatically changes Task state.

## 4. Work organization and navigation

- The browser tab title is `Taskman · <Project name>` for the selected Project and `Taskman`
  when none is selected. It follows Project selection and renaming.
- The browser favicon is a white rocket on an indigo rounded square, matching the Project badge
  style. It identifies Taskman consistently across Projects, with SVG and ICO formats.
- The default view is list-first: an active Project selector and that Project's nested List tree
  beside a main Task table. A separate **Project tasks** row returns to its root Task view.
- An explicit Project or List URL selects its Project. Opening `/` restores this browser's last
  valid Project, otherwise it shows a neutral Project selector.
- Selecting a Project shows its direct Project Tasks. Selecting a List shows its direct Tasks;
  **Include child Lists** optionally adds descendant Tasks while preserving their source List.
- The Task table initially shows matching Tasks whose entire ancestor chain contains no other
  matching Task. An always-visible disclosure beside the readable ID expands a root's complete
  subtree across locations and statuses; nested rows keep their ordinary actions. Tree connectors
  inside the Task column show parentage, with softer separators within families and stronger
  separators between roots; IDs and other columns stay aligned. Desktop rows use continuous
  guides; stacked rows below 1280 CSS pixels use short title-only connectors to keep metadata clear.
  Guides cap indentation at six levels, sharing the final lane for deeper nesting. Hovering or
  focusing a row highlights the connectors throughout its displayed root family. See the
  [Task tree guide design](../specs/2026-10-27-task-tree-guides-design.md).
  Column headers are shown at viewport widths of 1280 CSS pixels and above. Below that,
  a Sort button beside Statuses offers ID, Name, Status, Priority and, when child Lists are
  included, Location. Both controls use icons only below 768 CSS pixels. The menu shares the
  column-header sort: choosing the current field reverses direction; a new field starts ascending,
  except Status and Priority which start descending. The selected field shows its direction.
  The selected column sort applies to roots and independently to each sibling group. Equal values
  use ascending Task IDs even when the primary sort is descending. Sorting directly by ID still
  follows the selected direction. Counts remain
  the number of filter matches, including matching Tasks hidden by collapse, and do not change
  merely because a group expands. Revealed branches stay expanded through status and Include child
  Lists changes, including promoted descendants and returning ancestors, and through sorting,
  updates and detail navigation. Collapsing a root forgets its entire branch; a remembered ancestor
  can reveal that branch again. Changing Project/List, missing-location recovery or reloading resets
  expansion. See the
  [expandable Task listing design](../specs/2026-10-27-expandable-task-listing-design.md).
- Include child Lists and Statuses persist as browser preferences across navigation. A Share
  control copies the current route with both filters encoded when a reproducible view is needed.
- Opening a Task shows a modal over the preserved list state. The selected Task is shareable in
  the URL.
- The authenticated navbar has a global Task jump search centered between Taskman and account
  controls. It searches ID or title across Projects and opens a selected result in Task detail.
  Wide screens show a result dropdown; narrow screens use a search button and full-width panel.
  A blank query shows no Tasks. The Task detail modal covers the navbar, so search is available
  after closing detail. Preserve the current Project/List backdrop when it can show the result;
  otherwise use the Task's owning List or Project root. API and CLI search may optionally narrow
  results to one Project. See the
  [global search design](../specs/2026-09-29-global-task-search-design.md).
- Every Create Task modal has an explicit same-Project Location. If its selected location or backdrop
  becomes unavailable, the ordinary form remains editable but Create is disabled until another
  location is chosen; submission resolves that location fresh. Creation stays on its current browse
  backdrop only when the new Task is visible by direct or Include child Lists scope, otherwise it
  browses the chosen location without changing the current scope preference.
- In Task creation and editing, Status and Priority share the Parent Task picker's chevron and
  dark option-list presentation, with selected-option checkmarks and keyboard navigation.
  The custom menus retain the existing form values, autosave, Done warning and conflict behavior;
  native controls remain the fallback and recovery controls stay disabled. See the
  [field picker contract](../specs/2026-10-28-form-controls-design.md#select-picker).
- The Due date/time popup stages calendar and time choices until Apply. Cancel, Escape or
  dismissal discards the popup draft; Clear is also staged until Apply. The editable field uses
  `DD.MM.YYYY HH:mm` with 24-hour time, for example `28.10.2026 17:45`, consistently across browsers.
  Valid text is normalized at the web-form boundary; invalid nonblank text remains visible for correction
  without changing the saved value. Blank/whitespace-only text clears the due date. Values remain
  local date/time values without timezone conversion.
  See the [Due picker contract](../specs/2026-10-28-form-controls-design.md#date-time-picker).
- If a selected List becomes unavailable while a dirty Task detail form is active, Taskman resolves
  the Task and its current location again, reconciles the unsaved input, and continues ordinary
  detail automatically without creating, updating, or moving the Task. The previous backdrop stays
  only when the Task remains visible there by direct or Include child Lists scope; otherwise detail
  follows the Task's current location without changing that scope preference. Status filters do not
  affect this route decision. After fresh reconciliation, each valid nonconflicted changed field
  resumes normal saving under fresh authority and reports its own Saving, Saved, Not saved, or
  failure state beside that field; conflicts retain their field-specific resolution notice.
- An open Move Task popover remains usable when its source row or destination changes: an unavailable
  destination must be replaced. Deliberately collapsing or filtering away its row closes Move without
  navigation. When an external update hides a surviving Task's row, Move reopens in detail at its
  fresh location without moving the Task. A descendant still rendered in an expanded group keeps its
  row anchor. If the Task no longer exists, the move closes with an error.
- Dirty detail input retained across location changes is temporary to the current live page process.
  When the Project, Task, or Task location cannot be resolved again, recovery provides copy and
  explicit discard controls. Try again is available when fresh authority may be restored; a
  confirmed missing Task offers only Copy and Discard. Reloading, leaving the page, or a replacement
  connection loses the retained input.
- The Task detail dialog has a dedicated header with Task identity, Share and Close, above the
  hierarchy, editor and discussion. Close remains reachable while content scrolls and retains the
  existing autosave and comment-draft departure safeguards.
- The collapsible parent-child hierarchy shows the connected tree with wrapping titles, Task IDs,
  visible status labels and dots, priority flags and labels (including muted None), connected
  nesting guides and a current-Task highlight. Metadata orders ID, status, then priority and may
  wrap on narrow rows. Priority colors match the main listing. Native
  nested lists, Task links and named disclosure buttons provide ordinary Tab/Enter/Space navigation.
  The selected Task and its ancestors remain expanded with decorative markers; optional branches
  retain manual expansion while navigating within the same tree.
- A status filter beside the hierarchy heading is independent of the main listing. It retains
  Tasks with checked statuses, ancestors of matching Tasks, and the inclusive root-to-selected
  path. Filtering preserves parentage and manual expansion, including when matching descendants
  are in collapsed branches. All statuses start checked; choices survive navigation within the
  open dialog and reset when it closes. See the
  [hierarchy status filter contract](../specs/2026-10-28-hierarchy-status-filter-design.md).
- A sorting button beside the hierarchy status filter offers ID, Name, Status and Priority.
  Sorting applies independently to siblings at every depth with ascending ID tie-breaks, matching
  the main listing. On opening, copy the main listing's supported field and direction; fall back
  to Status descending when none is selected or the listing uses Location. New field selections
  start ascending for ID/Name and descending for Status/Priority; selecting the current field
  reverses direction. Name compares case-insensitively. Status and Priority use the main listing's
  semantic ranks. Choices remain independent of the listing during navigation, live refresh and
  panel hiding, and are reinitialized on the next dialog opening. Sorting preserves ancestry,
  filtering and manual expansion. The popup shows the selected field and direction and dismisses
  on selection, outside click or Escape, restoring trigger focus on selection or Escape.
  Verified by focused hierarchy unit/LiveView tests and Chromium checks for popup reachability,
  focus, dismissal precedence, responsive layout and reading-position preservation. Acceptance
  evidence: 114 focused server tests, six browser tests, and `mix precommit --max-cases 8` with
  1,392 passed and three existing skips; independent scoped review found no issues. Other browser engines and
  assistive-technology sessions remain unverified.
- At 1280 CSS px and wider, hierarchy, editor and discussion appear beside one another. From 1024
  through 1279 px, discussion follows the editor beside the hierarchy. Below 1024 px, the open
  hierarchy overlays the editor below the header. Opening the overlay focuses its hide control and
  excludes covered editor controls from Tab traversal. Escape closes an open hierarchy status or sort
  popup first, then hides the overlay before dialog
  departure; hiding it while focus is inside returns focus to the labeled hierarchy trigger.
  Selecting a Task retains panel openness. The panel preference lasts for the workspace session.
- Above 1440 CSS px, normal Task detail grows smoothly to a 2100 px dialog with a hierarchy
  up to 448 px wide. The editor panel retains its existing padding and is capped at 800 px
  whenever discussion is beside it. Discussion has at least 320 px of inner content width,
  excluding panel padding and divider, in that composition. It receives the remaining space,
  including space released by collapsing the hierarchy.
  Comments fill their cards with symmetric insets. Recovery and other dialogs keep their existing
  sizes. Exact allocation follows the [wide-layout specification](../specs/2026-10-28-task-detail-layout-design.md).
- The hierarchy heading stays outside its independently scrolling body. Four levels remain readable
  at 320 CSS px with wrapping and unbroken titles; deeper branches keep their indentation and use
  contained horizontal scrolling when needed to preserve approximately 180 px of title space.
  Navigation reveals an offscreen selected row without resetting a useful reading position on
  routine updates. Project/List backdrop and active filters survive hierarchy navigation.
- Discussion has Activity and Sessions tabs. Activity shows the Task's comments and a composer;
  Sessions shows a truthful empty state. Agent Session behavior awaits a separate accepted design.
  This tabbed presentation supersedes the earlier stacked Activity and Sessions sections.
- The hierarchy contains only parent-child work breakdown. A Related Tasks table contains Blocks /
  Blocked by links with add/remove controls in both groups. Cross-Project relationship entries
  identify their other Project. Each linked Task shows status and priority. On narrow displays, the
  main Task fields come first, followed by Related Tasks, then the Activity/Sessions tab panel.
- A docked right-detail layout is a future enhancement, not the MVP default.

### Task comments and Activity

- An authenticated user may append and read comments on any Task in the shared workspace,
  including Done and Will Not Do Tasks. An API key posts as its owning user. Each comment records
  the authenticated posting account while that account exists; callers cannot choose another
  account or supply a creation time. API and CLI posts may include an optional custom display name.
  That name is a label, not delegated identity or authority. Browser posts use the account login.
- The comment thread is append-only plain text, ordered by creation time and then comment ID.
  Submission trims surrounding whitespace, requires nonempty text, and limits it to 10,000 Unicode
  grapheme clusters. An optional custom name is trimmed, nonempty, and limited to 80 grapheme
  clusters. Internal line breaks remain; rendered markup is escaped. Comments are not edited,
  deleted individually, searched, or paginated in this MVP slice.
- While the account exists, comments show its current login email, preceded by the custom name in
  parentheses form when supplied: `Research agent (person@example.com)`. Changing the account
  email changes the login shown on earlier comments. Account deletion removes the account link
  without removing its comments or custom names; the login then reads `Deleted user` (for example,
  `Research agent (Deleted user)`). No email snapshot is kept in a comment.
- Posting a comment refreshes the Task's `updated_at` to the latest successful comment activity
  without changing its status, editable fields, or `lock_version`. A rejected post changes none of
  these. `updated_at` therefore reflects the most recent direct Task change or successful comment,
  while `lock_version` remains specific to editable Task fields. Comments are discussion, not Task
  edit attribution, lifecycle approval, generated Task-change history, or user ownership.
- Activity contains the ordered thread, a labelled multiline composer, and a truthful `No comments
  yet` empty state. Posting is explicit. The composer keeps its draft during ordinary Task updates
  and tab switches. Leaving Task detail with a nonempty unposted draft offers Go back, Submit
  comment and close, or Close and discard draft; full-page exits use the browser's best-effort
  unload warning. Sessions shares the same panel and remains an empty state until Agent Session
  behavior is designed.

## 5. Programmatic access and CLI

Taskman provides a versioned JSON API and an accompanying `taskman` CLI. Meaningful Project,
List, and Task operations available in the browser are also available through the CLI where
terminal use makes sense. Browser-only presentation state is not part of this parity contract.

The CLI:

- calls the running Taskman backend rather than accessing persistence directly;
- provides readable output by default and deterministic JSON for agent automation;
- has complete top-level and per-command help;
- generates Bash and Fish completions from the same command registry as help;
- includes an agent-onboarding command covering purpose, installation, configuration, and basic
  usage; and
- installs its version-matched agent skill to `~/.agents/skills/taskman-cli/`.

Later slices add API, CLI, help, Bash/Fish completion, and skill support alongside every new
meaningful UI operation. Every API request requires an expiring, revocable API key belonging to an
active user. The CLI supports a protected XDG configuration file and environment overrides for the
server URL and key.

Starting the backend through a future `taskman serve` command is a planned extension point, not an
MVP requirement of the initial CLI slice.

## 6. Authentication and hosted access

- Public self-registration is unavailable. A server-local command bootstraps an administrator, and
  administrators invite later users through an authenticated admin surface.
- An invitation verifies the email address and lets the user choose a password. Users may recover
  or change their password and confirm a new email address.
- Browser users authenticate through stored, revocable sessions. API clients authenticate with
  named API keys that are stored only as hashes and expire no later than one year.
- Users can manage their own sessions and API keys and permanently delete their own account after
  password confirmation. Administrators can invite, enable, disable, delete, promote, and demote
  users while the last active administrator remains protected.
- Administrators can change another user's email and explicitly confirm either the existing or new
  address. Changing a pending user's email immediately sends a fresh setup invitation.
- Disabling a user removes browser and API access immediately. All authenticated users otherwise
  see the same Project, List, and Task data.
- Account deletion permanently removes authentication data but leaves the shared Project, List,
  Task, and Task-comment workspace unchanged because those records are not user-owned. It nulls the
  deleted account link on retained comments, which then show `Deleted user` as their login.
- Password is the initial browser strategy. The Accounts boundary permits later magic-link and
  OAuth/OIDC strategies without changing domain ownership.
- Production uses an OTP release under systemd, a loopback Phoenix endpoint behind an HTTPS reverse
  proxy, private PostgreSQL, and transactional email for setup, confirmation, and recovery.

## 7. Task relationships

| Type | Scope and rules |
| --- | --- |
| **Blocks / Blocked by** | Directed prerequisite; may cross Projects; one edge per ordered Task pair; no self-link or cycle. |
| **parent-child** | Same-Project, acyclic work breakdown; a parent has many children and a child has at most one parent. |

Parent-child and Blocks may coexist for the same pair. A child may block its parent, but a parent may
never block its child. Relationships do not automatically transition Tasks. An unconfirmed move to
Done with an unresolved direct blocker warns and leaves status unchanged. A person may confirm the
move after reviewing that warning. API and CLI also accept confirmed blocker IDs on the first
request when the person already knows them, or a case-specific, one-request force confirmation
without listing blocker IDs. A blocker is resolved for the warning when it is Done or Will Not Do.
The warning identifies each unresolved direct blocker with its status and priority.
Confirmation remains valid when the blocker set is unchanged or only shrinks; a newly unresolved
blocker that was not confirmed requires a fresh warning.

Symmetric Relates to links are deferred beyond the MVP. They are not required in MVP storage,
browser, API, CLI, skill, or deletion behavior. See the
[relationship contract](relationships.md) for the scope rationale.

## 8. Agent Session integration

Agent Session implementation, provider selection, local execution context, work pickup, launch,
attachment, and recovery require a separate accepted design. The current Project model provides no
directory for agent execution. Agent activity does not automatically change Task state.

## 9. Deletion and human safeguards

Projects, Lists, and Tasks may be deleted permanently. Before any recursive deletion, the product
shows a detailed impact warning and requires a second explicit confirmation.

- Deleting a Project removes its Lists, Tasks, and internal relationships. It removes
  cross-Project relationship edges but preserves externally owned Tasks and names those effects.
- Deleting a List removes its descendants.
- Deleting a Task also removes its comments and incident relationships. If it has children, the user
  chooses either recursive child-subtree deletion or reparenting direct children to its former parent,
  or to the Project-level hierarchy. Recursively deleted children lose their comments; reparented
  children retain their comments, descendants, and List ownership.

## 10. Explicit MVP exclusions

- Product implementation and deployment; this document specifies the product only.
- Per-user domain ownership, synchronization, collaboration permissions, Task-edit attribution,
  generated Task-change Activity, and other multi-user workflows beyond authenticated access and
  the posting-account identity recorded on explicit comments.
- Public self-registration and authentication strategies beyond the initial password flow.
- Managed hosting, automated deployment, and container orchestration.
- Desktop packaging and managed Workspace creation, selection, reuse, or lifecycle.
- Agent Session integration, provider selection, local execution context, work pickup, launch,
  attachment, and recovery until a separate design is accepted.
- Symmetric Relates to Task links.
- Configurable workflows, global dashboards, advanced search beyond the bounded Task jump search,
  saved views, analytics, import, and export.

## 11. Related documents

- [Domain model and glossary](domain.md)
- [Task relationship semantics](relationships.md)
- [Superseded Agent Session workflow](agent-sessions.md)
- [Projects without primary directories](../specs/2026-09-10-projects-without-primary-directories-design.md)
- [MVP roadmap](../planning/roadmap.md)
- [Cosmos capability research](../research/cosmos-capabilities.md)
- [Emdash and Auggie capability research](../research/emdash-auggie-capabilities.md)
- [Navigation and MVP visual guidance](../prototypes/navigation.html)
- [Authenticated hosted access and release deployment](../specs/2026-09-02-authenticated-hosted-access-design.md)
