# Historical Handoff: Agent-Aware Project Manager Wayfinder

Date: 2026-07-14

## Completion status

The Wayfinder effort for the greenfield **Agent-Aware Project Manager** is complete. It produced the
resolved MVP product specification, including its role, capabilities, workflows, domain vocabulary,
interaction model, integration boundary, and explicit exclusions.

It was completed in a separate Notes repository. This document preserves its product-discovery
context; implementation planning and delivery now occur in `Incanus3/taskman`.

## Historical tracker artifacts

The Beads map **Chart the Agent-Aware Project Manager MVP** (`notes-kf1`, label `wayfinder:map`)
belonged to the separate Notes repository. Its issues are not present in this repository's Beads
store and must not be recreated or resumed here.

The completed map contained these child tickets:

| Ticket name | Beads key | Type | State |
| --- | --- | --- | --- |
| Define the core domain model and Task lifecycle | `notes-kf1.1` | grilling | Closed |
| Research Cosmos session launch and deep-link capabilities | `notes-kf1.2` | research | Closed |
| Prototype Project, List, Task, and Agent Session navigation | `notes-kf1.3` | prototype | Closed |
| Define the Agent Session launch and return workflow | `notes-kf1.4` | grilling | Closed |
| Synthesize the high-level MVP product specification | `notes-kf1.5` | task | Closed |
| Define Task relationship semantics and constraints | `notes-kf1.6` | grilling | Closed |

The completed dependency route was:

- **Define the core domain model and Task lifecycle** is resolved; it unlocked the navigation
  prototype and partly unlocked the Agent Session workflow.
- **Research Cosmos session launch and deep-link capabilities** is resolved; it supplied the remaining
  prerequisite and unlocked the Agent Session workflow.
- The navigation prototype, Agent Session workflow, and Task relationship semantics supplied the
  remaining inputs to final specification synthesis.
- The final synthesis ticket wrote the resolved MVP product specification.

The map is closed. Its resolution comments link the canonical glossary at
`docs/CONTEXT.md` and research asset at
`docs/research/cosmos-session-launch-capabilities.md`; the throwaway navigation artifact remains at
`docs/prototypes/agent-aware-project-manager-navigation-prototype.html`.

## Settled charting context

The map body is canonical for scope. The following preserves important interview detail and nuance
for the sessions that resolve its tickets.

### Product role and audience

- MVP is single-user, but the future data model should not make team support impossible.
- It is both a system of record for planned, active, and completed work and a lightweight launcher
  for external agent work.
- It is not an embedded agent runtime. Agent conversations and execution UI remain in the external
  provider.
- The canonical journey is: create a Project for a directory; organize Tasks; choose a Task; work
  personally or launch an Agent Session; return to related sessions; review the result; mark the
  Task done.
- An ended Agent Session is a contribution or attempt, not proof that the Task is complete. Human
  review remains the boundary before Done.

### Delivery and persistence

- MVP is a locally started web application used in a browser.
- Data persists locally on one machine.
- Public hosting, authentication, remote access, synchronization, collaboration, and desktop
  packaging are outside MVP.
- Hosted deployment and Electron-like wrapping are possible future delivery modes, not MVP
  requirements.

### Resolved domain model and Task lifecycle

The canonical detailed vocabulary is in `docs/CONTEXT.md`. The following
is the essential resume context:

- A **Project** has exactly one required primary local directory; any local directory is valid and
  repository metadata is detected when present. It owns an unbounded, acyclic tree of **Lists** and
  its **Tasks**. **Workspace** remains reserved for a future execution location or isolated checkout.
- A List has exactly one parent: its Project or another List in that Project. A Task has exactly one
  owning location at a time: directly under its Project (list depth zero) or in one List. Tasks may
  move only within their current Project and never belong to multiple Lists or span Projects.
- A Task is intended work. It has zero or more **Agent Sessions**; every Agent Session belongs to one
  Task, is an external attempt, and may not be shared or moved. Human work has no Human Session model.
- The fixed lifecycle is **Icebox → Pending → In Progress → In Review → Done**. In Review may return
  to Pending or In Progress. Done and Will Not Do are terminal; a human must explicitly mark Done
  after review. An Agent Session ending never completes a Task.
- A new Task requires a non-empty title and defaults to Pending, unless explicitly created in Icebox.
  Description, local due date-time, and checklist are optional. Priority is required and exactly one
  of None, Low, Medium, High, or Urgent; None is the only not-prioritized value. Checklists and due
  date-times never trigger status changes. Timezone behavior beyond the current system timezone is
  outside MVP.
- Projects, non-empty Lists, and Tasks with Agent Sessions may be deleted recursively. Before deletion,
  show a detailed impact warning and require a second explicit confirmation button.
- Explicit Task relationships are in MVP scope and are distinct from List ownership. The vocabulary is
  directed Blocks / Blocked by, symmetric Relates to, and parent-child. Parent-child is an acyclic,
  same-Project work-breakdown relationship: one parent may have many children, while each child has at
  most one; it does not alter List ownership or status. The resolved details are in
  `docs/TASK_RELATIONSHIPS.md`.

### Resolved Cosmos integration research

The detailed, cited record is `docs/research/cosmos-session-launch-capabilities.md`. The essential
constraints for later workflow design are:

- The first concrete integration remains Augment/Cosmos behind a provider-adapter boundary. Session
  UI, runtime, transcript, status, and controls remain external; this product records only the link.
- Cosmos documents interactive launch: the user selects an Expert, may choose a model, and writes or
  edits the prompt. Conversations persist indefinitely and the user can copy a Session link to return.
- Cosmos automations, including custom webhooks, can indirectly open a Session by sending raw event
  payload as its first message. They are not an editable human-reviewed handoff.
- The reviewed public docs do not describe an external create-session API or SDK, programmable
  editable prompt prefill, per-Session working-directory selection, a returned Session URL/ID callback,
  or a specified deep-link URL format.
- The MVP baseline is therefore manual handoff: prepare the Task instruction and read-only Project
  primary-directory path, open generic Cosmos, let the user choose/review/send, then save a user-copied
  Session link with label, provider, and created time. An ended Session never completes the Task.
- The Auggie SDK can launch a local agent process with model and workspace configuration, but it is not
  a Cosmos Session API. Treat it only as evidence for a future separate Auggie adapter.

### Resolved navigation interaction model

The reference artifact is `docs/prototypes/agent-aware-project-manager-navigation-prototype.html`.
It is throwaway planning material, not product code. Its styling is deliberately retained as reference.
It intentionally renders only the resolved list-first modal layout: the earlier split and task-focus
alternatives, variant URL behavior, and switcher were removed after their useful elements were merged
into this model. Do not restore them or interpret the artifact as an unresolved comparison.

- The primary layout is list-first: a left tree of Projects and nested Lists and a main Task table.
  There is no separate Direct Project Tasks node; selecting a Project itself shows its direct Tasks.
- Selecting a List shows its direct Tasks by default. An explicit Include child Lists control adds
  descendant Tasks, while the Location column preserves each Task's source List.
- Opening a Task presents a modal over the preserved table state. Closing restores that exact list
  position and filters; selected Task state should be shareable in the URL.
- The modal has a horizontally collapsible left parent-child hierarchy with visual nesting guides. It
  collapses as a left rail on smaller displays; only the selected Task row is highlighted.
- Core Task detail occupies the middle. A right Activity and Sessions rail holds session history, the
  launch action immediately above attaching an existing Session link, and the future docked-detail
  idea. A docked right detail is deferred rather than the MVP default.
- Parent-child relationships belong exclusively in the hierarchy. The collapsible Related Tasks table
  is for Blocks / Blocked by and Relates to links.

## Use in this repository

- Treat this document as historical product rationale, not an actionable tracker.
- Do not run `br` against, recreate, or resume the external Wayfinder issues from this repository.
- Use [the implementation-planning handoff](agent-aware-project-manager-implementation-planning.md)
  for current architecture decisions and implementation work.
