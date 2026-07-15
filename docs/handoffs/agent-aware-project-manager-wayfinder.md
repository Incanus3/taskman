# Handoff: Agent-Aware Project Manager Wayfinder

Date: 2026-07-14

## Purpose

Continue the Wayfinder effort for a greenfield product provisionally named **Agent-Aware Project
Manager**. The destination is a high-level MVP product specification covering the product role,
use cases, capabilities, workflows, domain vocabulary, interaction model, integration boundary,
and explicit exclusions.

This Notes repository contains only the planning map and future planning assets. The product does
not exist yet and will eventually live in another repository. Implementation is outside this map.

## Canonical tracker artifacts

The canonical source is the repo-local Beads map **Chart the Agent-Aware Project Manager MVP**
(`notes-kf1`, label `wayfinder:map`). Load that map at low resolution first; fetch child ticket
bodies only as needed.

The map has these child tickets:

| Ticket name | Beads key | Type | State |
| --- | --- | --- | --- |
| Define the core domain model and Task lifecycle | `notes-kf1.1` | grilling | Closed |
| Research Cosmos session launch and deep-link capabilities | `notes-kf1.2` | research | Closed |
| Prototype Project, List, Task, and Agent Session navigation | `notes-kf1.3` | prototype | Closed |
| Define the Agent Session launch and return workflow | `notes-kf1.4` | grilling | Ready |
| Synthesize the high-level MVP product specification | `notes-kf1.5` | task | Blocked |
| Define Task relationship semantics and constraints | `notes-kf1.6` | grilling | Ready |

Dependency route:

- **Define the core domain model and Task lifecycle** is resolved; it unlocked the navigation
  prototype and partly unlocked the Agent Session workflow.
- **Research Cosmos session launch and deep-link capabilities** is resolved; it supplied the remaining
  prerequisite and unlocked the Agent Session workflow.
- The navigation prototype is resolved. The Agent Session workflow and Task relationship semantics both
  block final specification synthesis.
- The final synthesis ticket is an explicit exception to Wayfinder's planning-only default. It may
  write the destination specification as a Markdown asset in this repository.

The domain-model, Cosmos-research, and navigation-prototype tickets are resolved. Their resolution
comments link the canonical glossary at
`docs/CONTEXT.md` and research asset at
`docs/research/cosmos-session-launch-capabilities.md`; the throwaway navigation artifact is at
`docs/prototypes/agent-aware-project-manager-navigation-prototype.html`. The map has one linked gist
for each. `br ready` now returns the Agent Session workflow and Task relationship semantics tickets.

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
  most one; it does not alter List ownership or status. Its remaining semantics are in the dedicated
  **Define Task relationship semantics and constraints** ticket.

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

## Fog still recorded on the map

None. All remaining in-scope decisions are already precise child tickets.

## Operational cautions

- This repository's tracker is `/home/jakub/Notes/.beads`. Normal `br` commands from the repository
  root now discover it correctly.
- A parent tracker also exists at `/home/jakub/.beads`. The map was initially created there by
  mistake, then deleted and recreated locally. Deleted audit tombstones remain in the parent tracker;
  do not resume those issues.
- Always refer to maps and tickets by title in human-facing text. Use Beads keys only for CLI lookup.
- Never resolve more than one Wayfinder ticket in a session. After a ticket is finished, report it,
  encourage a commit, and wait for explicit approval before beginning another.
- Claim a chosen ticket before doing any work. For a HITL ticket, the agent must not answer the
  human side of the interview.
- After resolution, post the answer as a resolution comment, close the ticket, append only a linked
  one-line gist to the map's **Decisions so far**, update fog/scope, create and then wire any newly
  surfaced tickets, and run `br sync --flush-only`.

## Recommended next session

Take exactly one Ready ticket and claim it before work. Unless the user names a ticket, Wayfinder order
now selects **Define the Agent Session launch and return workflow**. It is a HITL
grilling decision. Start from the manual Cosmos-handoff baseline above; do not revisit or invent an
undocumented automatic Cosmos Session-control API. It must settle the exact provider-adapter and
user-return flow without implementing the product.

**Define Task relationship semantics and constraints** is also now Ready. It is a separate HITL
grilling decision that must finish relationship cardinality, cross-Project, cycle, and deletion rules.
Do not resolve it while working the Agent Session workflow.

## Suggested skills

- `wayfinder` — mandatory for map loading, claiming, one-ticket pacing, resolution, and frontier
  maintenance.
- `grilling` — for the later Agent Session workflow and other HITL decisions; ask one question at a
  time and recommend an answer.
- `domain-modeling` — use the canonical glossary, challenge conflicting terms, and capture genuinely
  new resolved vocabulary as it crystallizes.
- `research` plus the official Augment documentation guide — only for newly precise external-platform
  questions surfaced later.
- `prototype` — consult its completed artifact only as the primary source for the resolved navigation
  model; do not treat its throwaway HTML as production code.
