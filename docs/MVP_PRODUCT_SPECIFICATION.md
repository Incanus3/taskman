# Agent-Aware Project Manager — MVP Product Specification

**Status:** Resolved product specification

## 1. Product role

Agent-Aware Project Manager is a single-user, locally started web application for planning work in
local Projects and coordinating that work with local Auggie agent sessions. It is the system of
record for Projects, Lists, Tasks, Task relationships, and linked Agent Sessions—not an embedded
agent runtime.

The core journey is: create a Project for a local directory; organize and perform Tasks; launch or
attach an Auggie Session for a Task when useful; return to the Task and Session context; review the
result; then explicitly mark the Task Done.

## 2. Users, delivery, and persistence

- One person uses the locally started application in a browser.
- All product data persists on that machine.
- A Project has exactly one required primary local directory. Any local directory is valid;
  repository metadata is detected when present.
- The application is neither hosted nor multi-user in the MVP. Its model should not preclude a later
  team-capable evolution.

## 3. Core domain model

| Concept | MVP definition |
| --- | --- |
| **Project** | Top-level work container with one primary directory, an unbounded acyclic tree of Lists, and Tasks. |
| **List** | Nested organizational container belonging to exactly one Project. It can contain Lists and Tasks. |
| **Task** | Intended work, owned by exactly one Project and one location: directly under that Project or in one List. |
| **Agent Session** | A first-class record of an Auggie work attempt belonging to exactly one Task; it never proves completion. |
| **Checklist** | Ordered, informational completion markers on a Task. |
| **Task relationship** | A Blocks / Blocked by, Relates to, or parent-child association; it is independent of List ownership. |

Tasks may move only between locations in their current Project. A Task starts in **Pending** unless
explicitly created in **Icebox**. Its fixed lifecycle is **Icebox → Pending → In Progress → In
Review → Done**; In Review may return to Pending or In Progress. **Will Not Do** is the other
terminal state. A human explicitly transitions state and reviews work before marking it Done.

Task priority is required and exactly one of **None**, **Low**, **Medium**, **High**, or **Urgent**.
Description, local due date-time, and checklist are optional. Neither checklist progress, due date,
nor an Agent Session automatically changes Task state.

## 4. Work organization and navigation

- The default view is list-first: a left tree of Projects and nested Lists plus a main Task table.
- Selecting a Project shows its direct Project Tasks. Selecting a List shows its direct Tasks;
  **Include child Lists** optionally adds descendant Tasks while preserving their source List.
- Opening a Task shows a modal over the preserved list state. The selected Task should be shareable
  in the URL.
- The modal has a collapsible left parent-child hierarchy with nesting guides, central Task detail,
  and an Activity and Sessions rail.
- The hierarchy contains only parent-child work breakdown. A Related Tasks table contains Blocks /
  Blocked by and Relates to links. Cross-Project relationship entries identify their other Project.
- A docked right-detail layout is a future enhancement, not the MVP default.

## 5. Task relationships

| Type | Scope and rules |
| --- | --- |
| **Blocks / Blocked by** | Directed prerequisite; may cross Projects; one edge per ordered Task pair; no self-link or cycle. |
| **Relates to** | Symmetric contextual association; may cross Projects; one relationship per unordered pair; no self-link. |
| **parent-child** | Same-Project, acyclic work breakdown; a parent has many children and a child has at most one parent. |

Relationship types may coexist for the same pair. A child may block its parent, but a parent may
never block its child. Relationships do not automatically transition Tasks. Moving a Task with an
unresolved direct blocker to Done requires an explicit warning confirmation; a blocker is resolved
for that warning when it is Done or Will Not Do.

## 6. Agent Session integration

### Auggie ACP adapter

The only supported MVP provider is **Auggie (ACP)**, shown in a one-option provider selector behind
an extensible provider-adapter boundary. The adapter starts, discovers, validates, and resumes local
sessions only in the Task Project's absolute primary-directory path. No Task or Session can override
that directory.

The adapter capability-negotiates optional model choices, discovery or validation, provider-supplied
session names, and recovery. It persists the opaque Auggie session ID, provider name when available,
resolved model or provider-default marker, created time, directory, initial instruction, optional
local label, and last-known recovery status.

Recovery status is **available**, **unavailable**, or **not yet checked**. It updates only on launch,
validated attachment, or explicit Resume; it is not live execution status.

### Launch, attach, and return

1. From a Task's Sessions rail, the user chooses **Launch Agent Session**, reviews the read-only
   primary directory, optionally selects a capability-reported model, and reviews an editable,
   generated instruction.
2. The instruction includes the Task title, description, checklist, state, priority, and a concise
   directory reminder. The user must confirm the exact text before it is sent.
3. The user may optionally edit a generated local label. The product creates the Session record only
   after ACP returns its opaque session ID; a creation failure leaves no record.
4. Without a local label, the list uses Auggie's supplied name, or **Auggie session · short ID**.
5. A user may instead choose **Attach Auggie Session**, paste an existing session ID, and attach it
   only after adapter validation in the same Project directory. A label is not required.
6. Session rows show their label, provider name when distinct, model/default marker, created time,
   and last-known recovery status.
7. **Resume** is explicit: the product shows stored launch context and an editable next message,
   then performs capability-supported recovery. Unsupported or failed recovery is clearly reported;
   no link or transcript is fabricated.

Agent Session actions never change Task lifecycle state. They support human work; they do not finish
it.

## 7. Deletion and human safeguards

Projects, Lists, and Tasks may be deleted permanently. Before any recursive deletion, the product
shows a detailed impact warning and requires a second explicit confirmation.

- Deleting a Project removes its Lists, Tasks, Agent Sessions, and internal relationships. It removes
  cross-Project relationship edges but preserves externally owned Tasks and names those effects.
- Deleting a List removes its descendants.
- Deleting a Task removes its Agent Sessions and incident relationships. If it has children, the user
  chooses either recursive child-subtree deletion or reparenting direct children to its former parent,
  or to the Project-level hierarchy. Reparented children retain descendants and List ownership.

## 8. Explicit MVP exclusions

- Product implementation and deployment; this document specifies the product only.
- Authentication, public hosting, remote access, synchronization, collaboration, permissions, and
  other multi-user workflows.
- Desktop packaging and managed Workspace creation, selection, reuse, or lifecycle.
- External Cosmos Session and Emdash task integrations; browser/deep links; arbitrary external links;
  and providers beyond Auggie ACP.
- Per-Session directory selection, embedded transcripts, live supervision or live agent status,
  execution monitoring, result streaming, and automatic Task-state transitions.
- Configurable workflows, global dashboards, advanced search, saved views, analytics, import, and
  export.

## 9. Decision records

- [Domain glossary](CONTEXT.md)
- [Agent Session launch and return workflow](AGENT_SESSION_WORKFLOW.md)
- [Task relationship semantics and constraints](TASK_RELATIONSHIPS.md)
- [Auggie and Emdash integration research](research/emdash-and-auggie-orchestration-capabilities.md)
- [Cosmos capability research](research/cosmos-session-launch-capabilities.md)
- [Navigation prototype](prototypes/agent-aware-project-manager-navigation-prototype.html)