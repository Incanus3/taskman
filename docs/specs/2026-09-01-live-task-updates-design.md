# Immediate Cooperative Workspace Updates Design

**Status:** Approved  
**Date:** 2026-09-01

## Context

Taskman is intentionally useful to humans in the browser and to agents through the versioned JSON
API and its `taskman` CLI client. Those clients use the same public Project, List, and Task contexts,
but a connected LiveView currently learns about persisted changes only when it performs the
mutation or later reloads its route. A Task changed from another browser tab, an API client, or the
CLI can therefore remain stale on screen. Project creation and List creation or rename can likewise
leave workspace navigation stale.

Task detail is URL-backed and contains an autosaving form. Reapplying the route or replacing all
LiveView state in response to an external change would be disruptive: it could close the modal,
discard a draft, reset focus, or overwrite a field that the human is actively editing. Immediate
visibility consequently requires both change notification and field-aware state reconciliation.

The current product remains locally started and has no authentication or multi-user identity.
“Another human” in this design means another connected browser session or tab. The design does not
add accounts, permissions, remote hosting, or collaborative presence.

The active delivery issue is `tas-live-task-updates-egu`. This feature is intentionally inserted
outside the current roadmap because frequent agent-driven workspace updates make stale browser
state a current usability problem.

## Outcome

Connected Project views reflect supported Project, List, and Task mutations made by other browser
sessions, JSON API clients, and the CLI immediately, without navigation or modal interruption.

Success means:

- successful Project creation and List creation or rename notify every connected Project view;
- successful Task creation, update, parent change, and movement notify every other connected view
  of the same Project;
- Project and List navigation is refreshed from canonical persistence while retaining expansion
  state;
- visible Task rows, counts, and hierarchy state are refreshed from canonical persistence using
  the view's current filters and sorting;
- open Project-creation, List-creation, List-rename, Task-detail, and Task-creation forms retain
  focus and local draft state;
- fields without unsaved local edits in an open Task-detail form update to the latest persisted
  values;
- a same-Task-field race never silently overwrites either participant's value;
- a human can resolve a same-Task-field conflict inline by accepting the latest persisted value
  or explicitly keeping the local value;
- failed or rolled-back mutations emit no change event; and
- focused context, LiveView, API, and CLI tests plus `mix precommit` pass.

## Scope

### Included

- A workspace-wide Phoenix PubSub topic for Project and List mutations.
- Project-scoped Phoenix PubSub topics for Task mutations.
- Structured events for Project creation, List creation and rename, and Task creation, ordinary
  field updates, parent changes, and movement.
- Publishing from the shared Project, List, and Task contexts after successful persistence.
- Connected LiveView workspace subscription and selected-Project subscription lifecycle.
- Canonical refetch and LiveView stream reconciliation after external events.
- Preservation of navigation expansion and open Project, List, and Task forms with their local
  interaction state.
- Task optimistic locking.
- Automatic retry of concurrent writes that touch disjoint fields.
- Field-level conflict state and inline resolution for the Task-detail autosave form.
- API and CLI conflict behavior consistent with the shared context result.
- Focused concurrency and propagation verification.

### Excluded

- Authentication, user identity, attribution, cursors, and collaborative presence.
- Remote hosting or general multi-node deployment work.
- A durable event log, transactional outbox, external message broker, or webhook delivery.
- Periodic polling as a second notification channel.
- PostgreSQL triggers or direct-SQL change capture.
- Project update or deletion and List movement, reparenting, or deletion, which are not currently
  supported product mutations.
- Project and List optimistic locking or field-level conflict resolution. Their existing
  last-write-wins behavior remains unchanged.
- Task deletion, which is not yet a supported product operation.
- Character-by-character collaborative editing, operational transforms, or CRDTs.
- A global activity feed or audit history.
- API clients subscribing to server events; API and CLI clients remain request/response clients.

## Architecture

### Context-owned change notification

`Taskman.Projects`, `Taskman.Lists`, and `Taskman.Tasks` remain the mutation boundaries shared by
LiveView and the JSON API. A context-neutral `Taskman.ChangeNotifications` module owns event
construction, workspace- and Project-topic naming, subscription, and publication. Web modules do
not publish mutation events themselves.

The event contract contains only routing and reconciliation metadata:

- entity: `project`, `list`, or `task`;
- operation: `created`, `updated`, or `moved`;
- owning Project ID;
- entity ID;
- the persisted Task `lock_version` for Task events; and
- the set of fields changed by the mutation.

For a Project event, the owning Project and entity IDs are the same. An event does not serve as an
authoritative entity representation. Consumers refetch through the relevant contexts, which makes
duplicate or out-of-order notifications harmless and keeps API representation concerns outside the
event boundary.

Project creation and List creation report every persisted presentation or ownership field. List
rename and ordinary Task updates report the actual changeset fields. Task creation reports every
persisted editable or ownership field relevant to presentation. Parent mutation includes
`parent_task_id`, and movement includes `list_id`. Events use atoms inside the application; no
public JSON event schema is introduced.

### Publish only after persistence succeeds

Every supported public Project, List, and Task mutation publishes exactly once after its database
operation or enclosing transaction has returned success. Publication never occurs inside a
transaction that could still roll back. Validation errors, not-found outcomes, unchanged moves,
stale-write conflicts, and unexpected persistence failures emit nothing.

The publisher uses `Phoenix.PubSub.broadcast_from/4` with the calling process as the sender. A
LiveView that performed a mutation already updates its own assigns through the mutation result and
does not process a redundant event. Other LiveViews receive browser-originated changes. API
controller processes and CLI-backed requests are not subscribers, so their events reach all
applicable connected Project views normally.

The database commit remains successful if an in-memory publication cannot be observed by a
subscriber. PubSub is an ephemeral invalidation mechanism, not part of the persistence
transaction. Reconnection reloads canonical state.

### Workspace and Project subscription lifecycle

Every connected `ProjectLive`, including the Project index, subscribes once to the workspace topic
for Project and List events. This matches the workspace navigation projection, which displays all
Projects and their Lists.

`ProjectLive` additionally subscribes to a Project's Task topic when a valid Project route has been
resolved. It tracks the currently subscribed Project ID, avoids duplicate subscriptions, and
unsubscribes before changing Projects or returning to the Project index.

The Task topic is Project-scoped rather than location-scoped because a Task can move between the
Project root and Lists, descendant-inclusive views span several locations, and an open modal can
continue showing a Task that no longer belongs to the visible location.

### External-event handling does not navigate

`ProjectLive.handle_info/2` handles matching workspace and Task events without calling
`handle_params/3`, pushing a patch, or clearing modal state.

For a workspace event, it refetches Projects and their Lists and rebuilds the Project and navigation
streams. Existing navigation expansion state, the Project-creation draft, and any open List form
remain intact.

When a List event affects the selected Project, the handler also refetches the selected Project and
List, recomputes the location path, and refetches the current visible Task slice. A List rename can
change the paths displayed on Task rows and location-based ordering. Active Task hierarchy, move,
and picker projections that display Lists refresh as applicable. Project creation only requires the
workspace navigation refresh.

For a matching Task event, the handler refetches the current visible Task slice using the existing
location, descendant, status-filter, and sort options, then resets the `:tasks` stream and recomputes
its empty-state assigns.

Resetting the stream is deliberately preferred to interpreting an event locally. Creation, status
changes, sorting fields, parent changes, and movement can all change membership or ordering. The
database query is the existing canonical projection and the modal is outside the Task stream, so a
stream reset does not close it.

When Task detail is open, the handler separately reconciles the selected Task. Relevant title,
status, parent, and creation events also refresh its hierarchy projection when they can affect the
rendered tree. Expansion state and other modal interaction state remain intact.

When a create modal is open, its form, chosen location, and parent-picker draft remain untouched.
The Task table behind it still refreshes. Candidate searches refetch through their existing query
path when the human next interacts with the picker.

An open List-creation or List-rename form likewise keeps its local draft while navigation behind it
refreshes. This increment does not reconcile concurrent edits to the same List field: if the human
subsequently submits a List rename, the existing last-write-wins behavior applies.

## Concurrency model

### Optimistic Task version

The `tasks` table gains a non-null integer `lock_version` with a database and schema default of `1`.
It is managed programmatically through Ecto optimistic locking and is not accepted by generic Task
changeset casting.

Every Task update, parent mutation, and movement verifies the version carried by the context's
input Task. On a stale write, the context reloads the current Task and compares only the fields the
caller intends to change with their values in the caller's stale baseline.

- If every intended field still equals its baseline value, the concurrent change was disjoint. The
  context reapplies the requested change to the current Task and retries with its latest version.
- If an intended field differs from its baseline, that field is a conflict and the context does not
  persist the caller's value.
- If the requested value already equals the current persisted value, that field is satisfied and
  does not conflict.

Retries are bounded. A second stale-write race returns a conflict rather than looping indefinitely.
Parent changes and movement re-run their existing ownership, hierarchy, and destination validation
against the reloaded Task before retrying.

The context conflict result contains the current Task and the conflicting fields. Web adapters can
react without querying the Repo or reverse-engineering an Ecto exception.

### Task autosave baseline

`TaskAutosave` retains:

- the latest canonical Task as the persisted baseline;
- the existing local draft and dirty-field set; and
- a map of field-level conflicts containing the latest persisted value.

On an external update to the selected Task:

1. Refetch the Task from `Taskman.Tasks`.
2. Replace every clean field in the form and baseline with its persisted value.
3. Preserve every locally dirty field in the draft.
4. If a dirty field also changed from its baseline, mark it conflicted and invalidate any pending
   autosave timer revision for that field.
5. Rebuild the form from the new canonical Task plus the preserved local draft.

An unrelated external change therefore appears immediately while typing continues. A same-field
change preserves the human's text but stops automatic persistence until it is resolved.

The same reconciliation runs when an attempted autosave receives a context conflict, covering the
race in which a write commits before its PubSub event is handled.

### Inline conflict resolution

A conflicted input shows a compact inline notice containing the latest persisted value and two
explicit actions:

- **Use latest** replaces the local draft value, clears its dirty and conflict state, and requires
  no write.
- **Keep mine** retries that one local value against the newest Task version. A further same-field
  race remains a conflict rather than being overwritten.

The ordinary autosave status reflects unresolved conflict and does not claim that the form is
saved. Conflict resolution does not open another modal, close Task detail, change its URL, or reset
unrelated dirty fields.

## API and CLI behavior

API and CLI writes continue to use partial updates: they express the fields to set now and do not
submit a long-lived full-record version. The controller's freshly fetched Task is the baseline for
the context operation, so optimistic locking protects writes that race during the request.

Disjoint concurrent field changes are retried transparently. A same-field race returns HTTP
`409 Conflict` using a stable `concurrent_update` error code and names the conflicting fields in
the existing error envelope. The CLI renders that API error through its existing human-readable
and JSON error paths and exits unsuccessfully; it does not retry a same-field conflict silently.

`lock_version` is internal concurrency metadata and is not added to the public Task representation
in this increment. Long-lived compare-and-set semantics for external clients would require a
separate conditional-request design.

Supported Project and List API and CLI mutations publish through their existing context calls.
Their request and response contracts do not change in this increment.

## Event ordering, recovery, and deployment assumptions

Events can be duplicated, delayed, or observed after a later event. Each handler refetches current
state, so event order does not become display order. The Task version included in Task events
permits an obvious stale-event fast path, but correctness does not depend on skipping it.

A disconnected browser can miss events. LiveView reconnection and route entry reconstruct all
state from PostgreSQL, so no polling or event replay is needed for the local product.

Phoenix PubSub supports the current single application node directly. A future hosted multi-node
deployment must make application-node discovery and PubSub distribution an explicit deployment
concern. Strong delivery guarantees or external consumers would justify a later transactional
outbox; they do not justify one now.

## Failure handling

- Failed Project, List, and Task mutations leave browser state unchanged and publish no event.
- A stale Task that disappears during refetch follows the existing not-found behavior; Task
  deletion itself remains outside this increment.
- A Project or List event whose entity cannot be found is handled by rebuilding navigation from
  canonical persistence; deletion remains outside this increment.
- A Task event for another Project is ignored even if delivered incorrectly.
- An event for the sending LiveView is normally excluded; handling one remains idempotent.
- An unknown or malformed internal event is ignored rather than changing route or modal state.
- Conflict resolution persistence errors retain the draft and surface the existing autosave failure
  state alongside the conflict where applicable.

## Verification

### Context and event tests

- Successful Project creation and List creation or rename publish the expected workspace event.
- Successful Task creation, update, parent mutation, and move publish the expected Project-scoped
  event.
- The calling subscriber is excluded while a second subscriber receives the event.
- Project, List, and Task validation failures, transaction rollbacks, unchanged moves, and Task
  conflicts publish nothing.
- Optimistic locking retries disjoint fields and returns same-field conflicts without data loss.
- Parent and movement retries preserve their current invariants.

### LiveView tests

- A mutation in one connected LiveView updates a second view immediately.
- Project creation appears in an already-connected Project index and workspace navigation.
- List creation and rename update navigation, selected-location labels and paths, and affected Task
  location projections.
- API and CLI Project creation, List creation and rename, and Task create, update, parent change, and
  move requests refresh an already-connected view.
- Navigation expansion and open Project-creation and List-creation or List-rename drafts survive
  workspace refresh.
- Status filters, sorting, descendant inclusion, empty states, and movement membership remain
  correct after stream reset.
- An unrelated external change updates an open detail modal without closing it or losing a dirty
  field.
- A same-field external change preserves the local draft, blocks its scheduled autosave, and shows
  inline resolution.
- Use latest and Keep mine produce their specified persisted and form states.
- The selected Task remains open when it moves out of the visible location or no longer matches a
  status filter.
- A create modal and its draft remain open while the table behind it changes.
- Every connected view retains its workspace subscription; switching Projects changes only the
  active Task subscription and prevents cross-Project Task updates.

Tests use process synchronization and received messages rather than sleeps. LiveView assertions use
stable element IDs and interaction outcomes rather than raw HTML or styling details.

### API and CLI tests

- Existing Project and List mutation responses remain unchanged while their successful context
  operations publish events.
- A same-field race maps to the `409 concurrent_update` API contract.
- Disjoint writes succeed with both values preserved.
- Human and JSON CLI modes expose a conflict without silently retrying it.

### Completion gate

Run focused tests while implementing, then run `mix precommit`. Browser acceptance should exercise
two tabs plus CLI Project, List, and Task updates and confirm that navigation, the table, and open
forms update as specified without navigation, focus loss, or modal closure.
