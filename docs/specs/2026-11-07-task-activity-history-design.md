# Task activity history

Status: Implemented, independently reviewed and operator accepted.

## Outcome and decision inventory

Activity combines existing comments with persisted changes to Task fields and relationships.
Description edits remain excluded. History starts when the feature is introduced; existing data
cannot reconstruct earlier changes or attribution.

| ID | Status | Choice | Cost or constraint | Rationale |
| --- | --- | --- | --- | --- |
| D1 | Accepted | Record changes in shared application workflows, atomically with mutations | Direct SQL changes are outside history | [Recording](#recording-and-concurrency) |
| D2 | Accepted | Show relationship changes on affected Tasks | One change can appear in several feeds, then disappear through consolidation | [Coverage](#coverage-and-affected-tasks) |
| D3 | Accepted | Attribute browser and API writes to the authenticated account | Callers must pass an actor; account locking serializes that user's writes | [Attribution](#attribution) |
| D4 | Accepted | Use one event with event/Task associations; order the mixed feed by latest edit time | Consolidation can move or remove an existing entry | [Storage](#storage-and-feed-order) |
| D5 | Accepted | Provide API/CLI activity queries, events-only by default with optional comments | Callers must opt in to the complete mixed timeline | [Interfaces](#interfaces-and-file-boundaries) |
| D6 | Accepted | Consolidate same-user edits to the same field or relationship within a sliding minute | Continuous edits can consolidate over more than one minute | [Consolidation](#one-minute-consolidation) |
| D7 | Accepted | Keep only consolidated history; delete entries whose final value equals the original | Intermediate work and fully undone changes are unrecoverable from history | [Consolidation](#one-minute-consolidation) |
| D8 | Accepted | A fully undone change by another user need not break an earlier sequence | The resulting history may join edits around an invisible intervention | [Consolidation](#one-minute-consolidation) |
| D9 | Accepted | Add one Task-created entry with creator and time | Initial field values do not get separate entries | [Coverage](#coverage-and-affected-tasks) |
| D10 | Accepted | Public Task writes own their transaction, as comments already do | Internal callers cannot wrap these use cases in a caller-owned transaction | [Recording](#recording-and-concurrency) |

D1–D10 reflect explicit operator approval, including events-only API/CLI defaults with optional
comments and the transaction boundary approved with the implementation plan.

## Existing behavior and constraints

Inspected baseline: `c5525a63f00dcab9b67819a7991073f7ffdbdf2a`, with no uncommitted changes
before design work. No external research is required for this design.

- [Tasks context](../../lib/taskman/tasks.ex) is shared by browser and API callers. The CLI uses
  the API. Mutations return changed-field names for PubSub notifications, not durable history.
- [Mutations](../../lib/taskman/tasks/mutations.ex) handles optimistic conflicts, retries,
  location changes, parent validation and Done confirmation. A retry can save against a newer
  baseline; the original caller's snapshot is not always the true previous value.
- [Blocking](../../lib/taskman/tasks/blocking.ex) serializes graph changes with `GraphLock`.
  Blocking links may cross Projects; parent links may not.
- [Comments](../../lib/taskman/tasks/comments.ex) already records authenticated authors and saves
  comments with a Task timestamp update transactionally. The
  [Activity component](../../lib/taskman_web/components/tasks/detail.ex) rendered only
  the comment stream at that baseline. [Reconciliation](../../lib/taskman_web/live/project_live/reconciliation.ex)
  distinguishes Task, comment and relationship notifications.
- The [current product contract](../product/mvp-spec.md#task-comments-and-activity) and
  [domain terminology](../product/domain.md#task-comments-and-activity) now include generated,
  consolidated history; the inspected baseline excluded it.
- [Development guidance](../guides/development.md) requires context-owned auditing, persistence
  outside the web layer, Ecto for Tasks, and UI/API/CLI parity. This is no domain migration.
- Task, List and Project deletion workflows are not exposed by the inspected contexts. Product
  [deletion semantics](../product/relationships.md#deletion-rules) describe future behavior;
  this work does not implement those workflows.

## Coverage and affected Tasks

Record actual persisted transitions, one event per changed field or directed blocking pair.
Multi-field API updates may therefore produce several entries in one transaction.

| Change | Consolidation identity | Affected Tasks |
| --- | --- | --- |
| Task creation | Never consolidated | Created Task |
| Title, status, priority, due date | Task ID + field | Changed Task |
| Owning List, including Project-level ownership | Task ID + `list_id` | Moved Task |
| Parent assignment, removal or replacement | Child ID + `parent_task_id` | Child, original parent and final parent, omitting nulls |
| Blocks added or removed | Blocking Task ID + blocked Task ID, in that order | Both endpoints, including across Projects |

Parent and child views use the same event with wording appropriate to the selected Task.
For example, a child shows “Parent changed from A to B”; A shows a child removed and B shows a
child added. Blocking views say “Blocks” or “Blocked by” according to direction.
The interface used to initiate a relationship change does not affect its identity or attribution.

Task creation produces one “Task created” entry with the authenticated creator and time. Initial
field values, including an initial parent, do not produce separate change entries. The creation
entry appears on the created Task only and never consolidates with later edits. Creation and its
entry commit atomically, including creation with an initial parent. There are no events for
description/checklist edits, comment-induced
timestamp updates, lock versions, derived counts, blocker availability, or linked Task status/name
changes in other Tasks' feeds. Project/List renames or List reparenting do not alter Task ownership
and do not fan out into Task history. Comments remain ordinary comments.

A consolidated parent transition A → B → C appears only on the child, A and C. Its appearance on
B disappears when B becomes an intermediate value. A return to A removes the whole event.
That follows the chosen loss of intermediate history rather than retaining partial relationship
receipts.

## One-minute consolidation

For each successful actual transition, inspect only the latest surviving event for its canonical
identity, ordered by last change time and event ID. Consolidate when all of these hold:

1. Its actor account ID equals the current authenticated actor's ID.
2. Its final value equals the actual previous value of this mutation.
3. The time since its last change is nonnegative and at most 60 seconds, inclusive.

Otherwise insert a new event. Do not search backwards past a surviving event by another user or
past a nonmatching value. Edits to other fields, other relationships, comments or descriptions do
not break the sequence. Different API keys owned by the same account count as the same user.

Consolidation retains the event ID, original value and first-change time, replaces its final value,
and advances its last-change time. If original and final values compare equal, delete the event
and its event/Task associations. Equality uses canonical domain values: IDs for relationships,
booleans for block presence, normalized strings/enums and normalized date values for fields.
Captured labels do not participate in equality. A rejected or unchanged request does not extend
the window, insert an event, or remove an entry.

No cursor, tombstone, delayed job or hidden detailed audit trail is retained. Following D8,
deleting another user's fully undone event may expose an older event as the latest candidate;
that candidate may consolidate if the same three conditions still hold. Its old last-change
time controls eligibility: the invisible intervention does not extend the window.

| Scenario | Result |
| --- | --- |
| Same user: Low → Medium at 10:00:00, Medium → High at 10:00:45 | One Low → High entry, last changed at 10:00:45 |
| Next same-field edit at 10:01:45 | Consolidates: exactly 60 seconds since the preceding edit |
| Next same-field edit at 10:01:45.000001 | New entry |
| Same user returns High → Low within the window | Delete the consolidated entry |
| Another user changes the same field and leaves it changed | Separate entry; previous user's next edit cannot merge past it |
| User A sets Low → Medium at :00; B sets Medium → High at :10 then High → Medium at :20; A sets Medium → Urgent at :30 | B's entry disappears; A's surviving entry becomes Low → Urgent |
| Same user adds then removes the same blocking pair within a minute | No event on either endpoint |
| Status edit warns about unresolved work, or optimistic update conflicts | Task and history both remain unchanged |

## Attribution

Pass the current authenticated account explicitly from LiveView and API through the public
mutation methods. Reuse the existing account eligibility boundary used for comments. Callers
cannot supply an actor ID or timestamps in Task update JSON. API-key ownership supplies the actor;
there is no new custom author label or agent identity system for change events.

Follow comment privacy semantics: retain an account foreign key, resolve the current login on
read, and show “Deleted user” after account deletion. Do not snapshot account emails. Historical
events whose account link is null cannot consolidate with any new actor. Missing/ineligible
actors at the application boundary return `{:error, :authentication_required}` without mutation;
API authentication and existing HTTP authentication responses remain authoritative.
Update internal fixtures and callers to provide eligible actors instead of inventing a silent
system actor. Actor requirements apply to create/edit/move/add-block/remove-block use cases.
Creation validation and initial-parent behavior otherwise remain unchanged.

## Recording and concurrency

Recommended approach: record in application mutation workflows. This captures domain meaning and
the authenticated actor directly, and shares behavior across browser, API and CLI. Database
triggers would cover direct SQL but require a separate actor propagation mechanism and domain
projection logic. Logging independently in each interface duplicates the policy and misses future
callers. Retaining every raw event then consolidating on read was rejected by D7.

The Task change and event insert/update/delete, including event/Task association changes, must
commit in one database transaction. Failure to persist history rolls back the Task or relationship mutation.
Preserve existing validation, warning, conflict and retry semantics. Capture before/after from
the successful persistence attempt's changeset/current records, not from the original stale
snapshot or submitted raw parameters.

Task-row writes serialize ordinary updates to a given Task. Keep that lock until history recording
completes; also lock the selected candidate event while modifying it. The existing graph lock
serializes parent and blocking changes, including the no-event-yet and deleted-event cases.
Do not introduce global serialization for ordinary edits or lock every Task whose feed displays
the event.
Validate/lock the eligible actor before acquiring graph or Task locks, consistently with comments'
actor-before-Task order. Creation, parent/status workflow transactions and ordinary updates must join a
single outer transaction rather than commit before history is written. Retain deliberate savepoint
handling for stale optimistic writes. The implementation plan must map these transaction paths
explicitly and test retry behavior.

The reused eligibility helper takes an exclusive account-row lock, so writes by the same user
serialize even across different Tasks, as comment posts already do. Accept that bounded cost for
consistent account deletion/disable behavior; this design does not change the Accounts lock API.

Use server-generated UTC microsecond timestamps sampled after the relevant mutation lock is
acquired, so queued work does not use request-start/transaction-start time for the window. No
timer is needed to finalize an entry. Earlier event time greater than the current time starts a
new entry. Preserve existing Task `updated_at`/`lock_version` semantics: logging and consolidation
do not touch them a second time; blocking changes and mirrored parent events do not touch their
associated Tasks.

Publish only after commit. Notifications contain invalidation identifiers, not history payloads.
Refresh every affected Project/Task feed, including Tasks whose event association was removed
by consolidation.
The initiating LiveView must refresh explicitly because current broadcasts exclude the sender.
If publication fails, the committed database remains authoritative and reload recovers the feed;
this retains existing best-effort PubSub semantics.

Planning refinement D10: reject public Task write calls inside a caller-owned Repo transaction
with `ArgumentError` before mutation, matching the existing comment API. An outer caller could
otherwise receive broadcasts before its transaction commits or rolls back. Current production
callers do not wrap Task writes; some concurrency tests do and must use owned-workflow lock gates
instead. Internal workflow transactions and SQL Sandbox ownership remain supported. Supporting
arbitrary caller transactions would require deferred post-commit publication machinery without
a present production consumer; D10 adopts the smaller explicit boundary.

The demo seed script preserves atomic reset and Task creation using the same context-owned
write coordinator and internal mutation/recording helper. It resolves its fixed synthetic demo
account and locks normal account eligibility before deleting data. It preserves existing account
roles and has no arbitrary-account fallback or additional administrator-role requirement. The
shared helper keeps seeding atomic without duplicating auditing or adding a public bulk-write API.

## Storage and feed order

Add `task_activity_events` and `task_activity_event_tasks` with focused Ecto schemas and persistence.
An event stores a canonical identity, kind, owning Task ID (child for parent edits; blocker for
blocking edits), actor reference, field when applicable, typed JSON old/new values, first-change
and last-change times. A unique `(event_id, task_id)` event/Task association makes one canonical
event visible in multiple feeds. Index identity + last-change time + ID for candidate lookup, and
associated Task ID + event ID for reading. Enforce allowed kinds/fields, required values and foreign
keys; implementation may use separate typed columns where they simplify constraints.

Capture Task reference titles and Project labels, and List names/paths alongside IDs when an
endpoint value enters the event. Preserve the original snapshot during consolidation and replace
the final snapshot with its new endpoint. These are historical labels; later renames do not rewrite
them or generate new events. Keep event content structured, not preformatted prose. Render it
escaped. Links resolve the current Task by ID; unavailable targets render historical text without
a broken navigation action. No description text enters event payloads.
Also retain the owning Task's reference snapshot and both blocking endpoint snapshots as event
context, so parent feeds can name the child and boolean blocking transitions can name both Tasks.
Refresh that context on consolidation; it never participates in value equality.

Deleting an event cascades its associations. Account deletion nullifies its actor link. Owner Task
deletion cascades its events and associations; deleting another associated Task removes only its
association. Historical relationship IDs in JSON do not require live targets. New deletion
workflows must explicitly decide events for surviving affected Tasks; they are outside this slice.

Keep comments in their existing table. A context query returns a mixed projection ordered oldest
first by effective time, kind and numeric ID: comment `created_at`, event `last_changed_at`, with
comments before events on an exact tie. IDs are namespaced, for example `comment-12` and `event-12`.
Consolidation can move an entry later in the thread or delete it. Reset the selected Task's mixed
LiveView stream after activity invalidation so both ordering and removals are correct. Keep the
composer draft, selected tab, pending description-departure coordination and existing scrolling
behavior intact. Show “No activity yet” only if neither comments nor events exist. Event rows show
the actor, readable transition and last-change time; first-change time remains available in the
projection/API. No pagination or new filters in this slice, matching current comment loading;
revisit pagination if actual feed size causes measurable cost.

## Interfaces and file boundaries

- `Tasks.list_activity(project, task, opts \\ [])` returns `{:ok, entries}` or
  `{:error, :not_found}` after existing Project scoping. `include_comments` defaults to false;
  the browser explicitly passes `include_comments: true` for its mixed timeline. Add focused activity workflow, schema and persistence modules under
  `lib/taskman/tasks/`; keep storage calls out of web modules.
- Integrate actor propagation and recording in `Tasks`, `Mutations`, `Blocking`, API Task/blocking
  controllers and LiveView editing/autosave/move/parent/relationship handlers. Do not put auditing
  inside a generic raw persistence helper or PubSub publisher.
- Replace the comments-only feed projection in the Task detail component and its LiveView
  workflow with mixed activity, retaining separate comment form ownership and existing comment
  APIs. Extend reconciliation for Task, relationship and comment activity, including draft
  departure capture/replay. Update focused styles only as needed for compact event rows.
- Add read-only `GET /api/v1/projects/:project_id/tasks/:task_id/activity`, returning
  `%{data: entries}`. Omitted `include_comments` or `include_comments=false` returns events only;
  `include_comments=true` returns comments and events in the same timeline order. Only literal
  `true` and `false` are accepted. Unknown query options or invalid values use HTTP 400 `invalid_request`;
  missing/scoped-out Task uses existing 404 `not_found`. Each entry has namespaced `id`, `kind`
  (`comment` or `change`) and `occurred_at`. Comment entries have a `comment` member containing
  the unchanged existing comment representation. Change entries have a `change` member with
  `type` (`task_created`, `field_changed` or `block_changed`), `task_id` (owner), `field` (null for creation and blocks),
  `actor: {user_id, login}`, `before`, `after`, `context`, `first_changed_at` and `last_changed_at`.
  `occurred_at` follows the effective time defined above; times use ISO 8601 UTC. The deleted-user
  actor has null `user_id` and login `Deleted user`. Scalar values use JSON strings/null, block
  values booleans, parent values Task reference objects/null, and List values location objects.
  Task references contain `id`, `project_id`, `project_name`, `title`; locations contain
  `project_id`, `project_name`, `list_id` (null for Project level), `path` (List name array).
  Creation has null `before`/`after` and equal first/last-change times.
  Due dates retain the existing Task API's naive ISO 8601 encoding. Context contains `task`
  (owner reference) and, for blocks, `blocking_task` and `blocked_task` references.
  There is no public event-write/update/delete endpoint.
- Add `taskman tasks activity list [--project PROJECT_ID] TASK_ID [--include-comments]`,
  supporting existing output modes and Project inference. Without the flag, return events only;
  with it, include comments in timeline order. Existing `tasks comments` commands and
  `tasks show --include-comments` keep their current behavior. Extend registry, renderer, help,
  Bash/Fish completion and bundled skill.
- Update canonical product Activity/relationship documentation and indexes when implementing.
  No new dependency or configurable time-window setting is needed.

## Verification

The design began from source inspection at the baseline above. The implementation now covers
transactional recording, consolidation, relationship event/Task associations, mixed browser
activity and read-only API/CLI queries. Verification covers the acceptance criteria below.
Implementation and integration review were scoped to the implemented work and relevant
interactions; they do not establish that unrelated branch code was reviewed.

Implementation acceptance requires atomic creation-entry and attribution tests, plus focused
database/context tests for every covered field,
both relationship directions and cross-Project affected Tasks; no-ops, validation failures, Done
warnings and conflicts; same-user/different-user and exact window boundaries; cancellation and
the D8 scenario; authoritative old values after retries; atomic rollback on event failure; and
concurrent writes without duplicate/lost history. Use a controlled clock seam rather than sleeps.
Verify that descriptions never enter event storage, including mixed description/field updates.

LiveView tests must establish mixed ordering, consolidation reorder/removal on both initiator and
subscriber, removal from the former parent's feed, retained comment drafts and tab state, and
existing description-departure event replay. API/CLI tests cover query scoping, representations, caller
attribution, events-only defaults, optional mixed timelines, invalid query options/values,
unchanged comments commands and help/completion parity. Direct browser inspection
checks compact readability and scrolling; add a browser test only for behavior lower layers cannot
prove. Run the development guide's CLI executable gate and `mix precommit` after implementation.

The completed implementation plan is retired. Successful retry baselines, transaction ownership,
invalidation of affected Task feeds, capture/replay and interface contracts remain specified above
and covered by the focused tests. The workstream is complete and accepted.

Local verification uses Elixir 1.20.4 / OTP 29. The final branch precommit passed 1,664 tests,
with three existing executable skips and 85 browser exclusions; Credo reported no issues.
All 59 affected browser cases passed in their intended modes. The
[browser-test review](../research/2026-11-08-browser-test-ownership.md) owns their reduction
and execution-mode evidence. The rebuilt executable gate passed all three tests. Focused CLI
regressions and the full CLI suite passed. The executable exercised both activity query modes against a
disposable synthetic backend. This establishes local behavior and binary parity, not live-backend
acceptance or the pinned release runtime. Direct browser checks at measured widths of 1536, 1024
and 488 CSS pixels established wrapping without horizontal overflow, local-time rendering,
same-ID reorder/cancellation with retained drafts, and preserved bottom/away-from-bottom scrolling.
The earlier nonfatal development module-redefinition diagnostic did not recur in a forced compile
with warnings as errors or a fresh asset build/digest; its cause remains unproven.

## Approval scope

D1–D10, the assembled design and implementation plan are approved. Independent implementation
and final integration reviews are complete. The operator accepted the completed workstream,
including scoped review and the atomic demo-seeding refinement. The branch was pushed under
separate authorization; merge and deployment require their own explicit authorization.
