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
  filtered by user, and the MVP has no collaboration permissions or attribution.
- Taskman can run as an OTP release behind an HTTPS reverse proxy on a dedicated server.

## 3. Core domain model

| Concept | MVP definition |
| --- | --- |
| **Project** | Top-level work container with an ID, name, description, icon, color, an unbounded acyclic tree of Lists, and Tasks. |
| **List** | Nested organizational container belonging to exactly one Project. It can contain Lists and Tasks. |
| **Task** | Intended work, owned by exactly one Project and one location: directly under that Project or in one List. |
| **Checklist** | Ordered, informational completion markers on a Task. |
| **Task relationship** | A Blocks / Blocked by, Relates to, or parent-child association; it is independent of List ownership. |

Tasks may move only between locations in their current Project. A new Task defaults to **Pending**,
and a user may explicitly select another lifecycle state during creation. Its fixed lifecycle is
**Icebox → Pending → In Progress → In Review → Done**; In Review may return to Pending or In
Progress. **Will Not Do** is the other terminal state. A human explicitly transitions state and
reviews work before marking it Done.

After a successful Task move, the browser stays on the current Project/List backdrop if the Task is
still in its direct or enabled descendant location scope. Otherwise it follows the Task to its new
location. A row move opens that location's Task list; a detail move keeps the same Task editor open
over that location. The Include child Lists setting is preserved, and status filters do not affect
the route choice.

Task priority is required and exactly one of **None**, **Low**, **Medium**, **High**, or **Urgent**.
Description, local due date-time, and checklist are optional. Neither checklist progress, due date,
nor external agent work automatically changes Task state.

## 4. Work organization and navigation

- The default view is list-first: an active Project selector and that Project's nested List tree
  beside a main Task table. A separate **Project tasks** row returns to its root Task view.
- An explicit Project or List URL selects its Project. Opening `/` restores this browser's last
  valid Project, otherwise it shows a neutral Project selector.
- Selecting a Project shows its direct Project Tasks. Selecting a List shows its direct Tasks;
  **Include child Lists** optionally adds descendant Tasks while preserving their source List.
- Include child Lists and Statuses persist as browser preferences across navigation. A Share
  control copies the current route with both filters encoded when a reproducible view is needed.
- Opening a Task shows a modal over the preserved list state. The selected Task is shareable in
  the URL.
- Every Create Task modal has an explicit same-Project Location. If its selected location or backdrop
  becomes unavailable, the ordinary form remains editable but Create is disabled until another
  location is chosen; submission resolves that location fresh. Creation stays on its current browse
  backdrop only when the new Task is visible by direct or Include child Lists scope, otherwise it
  browses the chosen location without changing the current scope preference.
- If a selected List becomes unavailable while a dirty Task detail form is active, Taskman resolves
  the Task and its current location again, reconciles the unsaved input, and continues ordinary
  detail automatically without creating, updating, or moving the Task. The previous backdrop stays
  only when the Task remains visible there by direct or Include child Lists scope; otherwise detail
  follows the Task's current location without changing that scope preference. Status filters do not
  affect this route decision. After fresh reconciliation, each valid nonconflicted changed field
  resumes normal saving under fresh authority and reports its own Saving, Saved, Not saved, or
  failure state beside that field; conflicts retain their field-specific resolution notice.
- An open Move Task popover remains usable when its source row or destination changes: an unavailable
  destination must be replaced, while a surviving Task whose row is no longer visible reopens in
  detail at its fresh location without being moved. If the Task no longer exists, the move closes
  with an error.
- Dirty detail input retained across location changes is temporary to the current live page process.
  When the Project, Task, or Task location cannot be resolved again, recovery provides copy and
  explicit discard controls. Try again is available when fresh authority may be restored; a
  confirmed missing Task offers only Copy and Discard. Reloading, leaving the page, or a replacement
  connection loses the retained input.
- The modal has a collapsible left parent-child hierarchy with nesting guides, central Task detail,
  and Activity and Sessions empty-state rails. Agent Session behavior awaits a separate accepted
  design.
- The hierarchy contains only parent-child work breakdown. A Related Tasks table contains Blocks /
  Blocked by and Relates to links. Cross-Project relationship entries identify their other Project.
- A docked right-detail layout is a future enhancement, not the MVP default.

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
  and Task workspace unchanged because those records are not user-owned.
- Password is the initial browser strategy. The Accounts boundary permits later magic-link and
  OAuth/OIDC strategies without changing domain ownership.
- Production uses an OTP release under systemd, a loopback Phoenix endpoint behind an HTTPS reverse
  proxy, private PostgreSQL, and transactional email for setup, confirmation, and recovery.

## 7. Task relationships

| Type | Scope and rules |
| --- | --- |
| **Blocks / Blocked by** | Directed prerequisite; may cross Projects; one edge per ordered Task pair; no self-link or cycle. |
| **Relates to** | Symmetric contextual association; may cross Projects; one relationship per unordered pair; no self-link. |
| **parent-child** | Same-Project, acyclic work breakdown; a parent has many children and a child has at most one parent. |

Relationship types may coexist for the same pair. A child may block its parent, but a parent may
never block its child. Relationships do not automatically transition Tasks. Moving a Task with an
unresolved direct blocker to Done requires an explicit warning confirmation; a blocker is resolved
for that warning when it is Done or Will Not Do.

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
- Deleting a Task removes its incident relationships. If it has children, the user
  chooses either recursive child-subtree deletion or reparenting direct children to its former parent,
  or to the Project-level hierarchy. Reparented children retain descendants and List ownership.

## 10. Explicit MVP exclusions

- Product implementation and deployment; this document specifies the product only.
- Per-user domain ownership, synchronization, collaboration permissions, activity attribution, and
  other multi-user workflows beyond authenticated access to the shared workspace.
- Public self-registration and authentication strategies beyond the initial password flow.
- Managed hosting, automated deployment, and container orchestration.
- Desktop packaging and managed Workspace creation, selection, reuse, or lifecycle.
- Agent Session integration, provider selection, local execution context, work pickup, launch,
  attachment, and recovery until a separate design is accepted.
- Configurable workflows, global dashboards, advanced search, saved views, analytics, import, and
  export.

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
