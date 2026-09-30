# Task blocking relationships and Done warning

**Status:** Implemented and operator accepted

**Date:** 2026-09-27

**Updated:** 2026-09-29

## Purpose and authority

This design covers directed Blocks / Blocked by links between Tasks in any Projects and explicit
confirmation when a human moves a Task to Done with unresolved direct blockers. The
[relationship product contract](../product/relationships.md) and
[MVP specification](../product/mvp-spec.md) govern behavior. The
[roadmap](../planning/roadmap.md) defers symmetric Relates to beyond the MVP.

The intended result is a Task detail where a user can manage prerequisites from either endpoint,
follow links across Projects, and deliberately complete work despite unresolved direct blockers.
Browser, API, CLI, help, completion, and the bundled agent skill must expose meaningful operations
together. Relationships never change Task status automatically.

The companion [global Task search design](2026-09-29-global-task-search-design.md) uses the same
ID/title term matching across all Projects. This specification keeps relationship rules and the
Project-scoped link picker together; global navbar search has its own UI and API/CLI contract.

## Current implementation and boundaries

| Existing area | Relevant behavior |
| --- | --- |
| Stack | Phoenix LiveView, Ecto, PostgreSQL, `/api/v1` JSON API, and Req-backed `taskman` escript. Accounts/API keys use Ash; Tasks remain in the Ecto context. |
| Task domain | `Taskman.Tasks` is the public boundary. `Taskman.Tasks.Mutations` owns writes and optimistic `lock_version` conflicts. `tasks.parent_task_id` stores parentage; parent changes acquire the graph lock before the Project row lock and validate ancestry and blocking direction. |
| Blocking graph | `task_blocking_links` stores directed edges. `Taskman.Tasks.Blocking` validates mutations under the graph lock and delegates storage to `BlockingPersistence`. Persisted status changes share that lock. |
| Task detail | Canonical URL-backed modal with parent-child sidebar, autosaved form, Related Tasks controls, and Activity/Sessions tabs. An unresolved-blocker warning retains the Done draft until confirmation or cancellation. |
| API and CLI | Search, relationship reads/mutations, and Done confirmation are delivered through the authenticated API and CLI. CLI response shapes are validated, and one registry drives help and Bash/Fish completion. |
| Deletion | Project, List, and Task deletion operations do not yet exist. The later deletion-safeguards slice owns their warnings and commands. |

The relationship/MVP product documentation remains authoritative. This specification owns the
delivered behavior, architecture, and verification limits.

## Scope and domain rules

### Link identity and validation

- Store one directed edge `blocking_task_id -> blocked_task_id` per ordered pair, including pairs
  across Projects. Task IDs are instance-wide; links do not change List ownership or parentage.
- Reject missing endpoints, self-links, duplicates, and any cycle in the complete Blocks graph.
  A reverse edge would be a two-Task cycle and is invalid.
- Parent-child and Blocks may coexist only in the allowed direction: a child may block its immediate
  parent, but a parent may never block its own child. Parent creation or change must reject a pair
  where that parent already blocks the child. Existing same-Project, acyclic hierarchy rules remain.

### Lifecycle

- All statuses remain human selectable. A direct blocker is resolved for the Done warning only at
  `done` or `will_not_do`. Indirect blockers do not trigger it.
- The warning applies when an existing Task moves from any non-Done status into Done. A Done no-op
  against the current persisted status or ordinary field edit needs no new confirmation. An
  explicitly submitted unchanged status still participates in optimistic conflict checks; a stale
  caller cannot treat a concurrent status change as a successful no-op. A newly created Task cannot
  yet have links.
- An unresolved incoming link may be added to an already Done Task without reopening it. The
  warning applies if that Task later moves back into Done from another status.
- Link, blocker-status, and parentage changes never change a dependent Task's status.

### Exclusions

Relates to, prerequisite discovery, automatic status gates, dependency ordering, bulk relationship
editing, and deletion operations are outside this increment.

### Operator decisions

The operator chose these behaviors during design:

| Decision | Selected behavior |
| --- | --- |
| Done warning | Browser confirmation supplies the displayed blocker IDs. API/CLI may supply known blocker IDs on the first request or after a warning, or use a case-specific, one-request force override. Confirmation remains valid when blockers resolve or disappear; a new unconfirmed blocker requires a fresh warning. |
| Graph coordination | One PostgreSQL transaction advisory lock for graph, parentage, and status writes; a named Elixir module attribute holds its fixed numeric key. |
| Already Done Task | Permit a new unresolved incoming link; leave status Done. |
| Cross-Project picker | Choose a Project, then search its Tasks; every whitespace term matches when it appears in the decimal ID or case-insensitive title, including when it appears in both. |
| Link removal | One explicit Remove click, without a second confirmation. |
| Open Related Tasks rows | Refresh immediately when a linked Task changes status, title, priority, or location, or a Project/List name in its displayed location changes. |
| Relationship read response | Return linked Task summaries, including priority, in the `blocks` and `blocked_by` arrays. Edge responses contain only the two named endpoint summaries, without duplicate scalar IDs; Done warnings reuse the summary. Show priority in Related Tasks and the warning. |
| Mutation direction | One canonical `Blocks` API route and CLI command family; Task detail still offers add/remove in both displayed groups. |
| Mutation target ID | Use the same `/blocks/:target_task_id` path for `POST` and `DELETE`; neither request has a body. |

Only current unresolved blocker IDs absent from the submitted confirmed IDs invalidate that
confirmation. A prior warning is not required for API/CLI clients that already know the blocker
IDs. Requiring exact set equality was superseded because resolving or removing a blocker does not
introduce a new reason to warn. The force override confirms the Done transition itself without
claiming that a particular blocker set was reviewed. This permits a single CLI command even when
the set changes concurrently.

## Architecture decision

Persist only actual directed links in an edge table. Check reachability recursively when creating a
link. Serialize graph, parentage, and status writes with one PostgreSQL transaction advisory lock;
ordinary Task and relationship reads do not need it. Acquire the fixed application lock inside the
write transaction before any Project or Task row lock. PostgreSQL releases it with the transaction.

This serializes those writes across Projects. The MVP expects few such mutations; measured
contention could justify changing the lock behind the same context boundary later.

| Rejected approach | Advantage | Reason to defer |
| --- | --- | --- |
| Closure table | Cheap reachability reads. | Link removal requires complex stored-path maintenance. |
| Serializable transactions with retries | More concurrent writes. | Parentage, links, and Done warnings would all need reliable whole-transaction retries. |
| Hashing a string for the advisory-lock key | Descriptive name in the SQL expression. | The named Elixir attribute already explains the key; hashing adds a collision possibility. |

`Taskman.Tasks.Blocking` owns application operations and graph invariants. `Taskman.Tasks` exposes
list, candidate search, add, and remove use cases. Candidate search uses the shared
`Taskman.Tasks.Search` predicate from the [global Task search design](2026-09-29-global-task-search-design.md),
restricted to the chosen Project. Web adapters call that context rather than
`Repo`; API and CLI translate the same domain results. Keep parentage and status writes in their
current Task workflow and add only the calls needed for shared rules. Do not introduce a generic
relationship framework or an Ash/Ecto hybrid domain model.

### File ownership

| Area | Responsibility |
| --- | --- |
| `lib/taskman/tasks/blocking*.ex` | Edge schema, storage queries, reachability and blocker projection, stable domain results. Keep the workflow and raw query roles separate in focused modules. |
| `lib/taskman/tasks/graph_lock.ex` | Shared graph-lock key and acquisition function used by link, parentage, and status write workflows. |
| `lib/taskman/tasks/search.ex` | Shared ID/title search predicate; the relationship picker applies a chosen-Project scope and excludes the selected Task. |
| `lib/taskman/tasks.ex`, `mutations.ex`, `hierarchy.ex` | Public use cases, Done confirmation and status write coordination, and existing parent validation integrated with blocking rules. |
| `lib/taskman/change_notifications*`, `lib/taskman_web/live/project_live/reconciliation.ex` | Local and cross-Project relationship invalidations and refetch. |
| `lib/taskman_web/components/tasks/`, `lib/taskman_web/live/project_live/tasks/` | Related Tasks presentation, picker, warning state, and events; keep persistence in `Taskman.Tasks`. |
| `lib/taskman_web/controllers/api/`, `router.ex` | Authenticated route parsing, data/error representation, and context translation. |
| `lib/taskman/cli/`, `priv/taskman_cli_skill/SKILL.md` | HTTP commands, response validation, readable/JSON output, registry-based help/completion, and agent guidance. |
| `priv/repo/migrations/`, matching `test/` directories | Storage migration and focused behavior/contract coverage. |

## Persistence and graph validation

### Storage

- Add `task_blocking_links` with a generated bigint primary key, `blocking_task_id bigint NOT NULL`,
  `blocked_task_id bigint NOT NULL`, and UTC timestamps. Task IDs are instance-wide unique; store no
  Project IDs on the link.
- Add a unique index on the ordered pair, a non-self check, and foreign keys to `tasks.id` with
  `ON DELETE CASCADE`. Deleting either endpoint Task removes the link; deleting the link never
  deletes a Task. Index each endpoint for incoming/outgoing reads and deletion impact.
- A link is never updated and has no stored status or resolution; blocker status comes from its Task.

### Write transactions and lock order

Use one dedicated transaction-level PostgreSQL advisory lock in the same transaction as each write
that can change the blocking graph, its parent-child constraint, or blocker resolution. Define
`@task_graph_advisory_lock_key {724_150, 1}` in
`Taskman.Tasks.GraphLock`, then acquire `pg_advisory_xact_lock($1, $2)` with those two values. The
numeric pair has no domain meaning. The attribute and its shared acquisition function explain its
purpose and reserve it for this graph. Every graph write path must use that function and key.
[PostgreSQL accepts](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)
one `bigint` or two `integer` key components, so the lock cannot directly use a string key.

The lock covers link add/remove, Task parent assignment or change, and any persisted change to a
Task's status. A Task can become a blocker at any time, so status writes cannot safely skip the
lock based on an earlier check for existing links. Creating a Task without a parent or links, and
editing only title, description, due date, priority, or List location do not need this graph lock.
A Task update that also changes status or parentage uses the lock for the whole atomic update.
Future Task, List, and Project deletion workflows must acquire it before deleting Tasks because
foreign-key cascades remove their incident links.

1. **Add:** Acquire the graph lock and reload both Tasks. Check self-link, duplicate, and immediate
   parent-child direction. Traverse outgoing edges from the proposed blocked Task; reaching the
   proposed blocking Task means the new edge would close a cycle. Insert only after all checks pass.
2. **Remove:** Acquire the lock, reload the exact ordered edge, and delete it atomically. A missing
   edge is not a successful no-op.
3. **Parentage:** Acquire the graph lock before the existing Project row lock. Reload relevant Tasks,
   reject a forbidden parent-to-child block, and retain existing parent-cycle and optimistic-lock
   checks.
4. **Status mutation:** Acquire the graph lock, then reload the Task. For a move into Done, read its
   incoming edges and blocker statuses under that lock before deciding whether to warn or persist.
   Other participating writes cannot change that blocker set until this transaction ends.

Only these context workflows may write edges, parentage, or status; future bulk mutations of those
fields must use the same lock. The lock order avoids deadlocks. Database constraints protect local
row invariants, but cannot alone reject a long cycle.

### Reads and scope

- Ordinary Task detail, list, API, and CLI reads of status or relationships use committed data
  without acquiring the graph lock. A warning is checked again in the later confirmation request.
- Return each linked Task's ID, title, status, priority, Project ID/name, and owning List path.
  Sort each direction by Project name, Task title, then IDs. Derive Project IDs from the endpoint
  Tasks when building these views and validating Project-scoped routes.
- On every operation, confirm the selected Task still belongs to the Project identified by
  `:project_id` in the URL. Identify a cross-Project target by exact Task ID. A stale or missing
  endpoint leaves storage unchanged.

### Change notifications

- After add/remove, publish relationship invalidations to both endpoint Projects, once if they are
  the same Project. Each event carries a sorted, deduplicated `task_ids` list of the local endpoints;
  a same-Project add/remove includes both IDs in one event.
- When a Task's status, title, priority, or location changes, keep its existing Task event and
  publish relationship invalidations for linked endpoint Tasks, including those in the same
  Project. Group linked endpoint IDs by Project; one event may invalidate several local Tasks.
- `Taskman.ChangeNotifications.Event` uses `entity: :relationship`, `operation: :invalidated`,
  `project_id`, and `task_ids`; `entity_id` is the smallest local ID and `fields` is empty.
  `ProjectLive.Reconciliation` refetches Related Tasks only when the open Task is in `task_ids`;
  detail also loads links on route entry.
  Never interpret a foreign Task event as a local Task update.
- On an existing workspace Project-name or List-name update event, refetch Related Tasks when
  detail is open so cross-Project names and owning List paths update immediately. This may refetch
  for an unrelated rename; no new persisted index or reverse location lookup is needed.
- Notifications only prompt refetches; duplicate events may coalesce, and a missed event cannot
  compromise persisted invariants.

Publications follow the context operation's own successful transaction. Current application callers
invoke these operations at the top level. As with existing Task events, wrapping an operation in an
outer transaction that later rolls back can publish an invalidation before that rollback; this is
not an outer-transaction after-commit guarantee.

## Task detail interaction

### Related Tasks display and navigation

- Place **Related Tasks** below the central Task fields, separate from the parent-child sidebar and
  Activity/Sessions tabs. Show **Blocked by** and **Blocks** groups, counts, and a truthful empty
  state. Each compact row shows title, status, priority, Project for cross-Project links, and an
  icon-only x-mark Remove button vertically centered in the row. Its accessible label and hover
  title identify the linked Task and group. Status and priority use the Task table's human-readable
  labels, such as `In Progress` and `High`, rather than raw enum values.
- On narrow displays, stack main Task fields, Related Tasks, then the Activity/Sessions tab panel
  in that order. Keep the parent-child hierarchy collapsible and separate.
- Link rows to Task detail. Retain current browse context within a Project where existing URL rules
  permit it. For another Project, open `/projects/:other_project_id/tasks/:other_task_id` and select
  that Project's root view. Neither navigation nor links change List ownership or hierarchy.
- Keep the section usable at the modal's narrow width without horizontal clipping. Support
  keyboard interaction and announce add errors near the control.

### Add and remove

1. Choose **Blocks** or **Blocked by** from the selected Task's perspective using the group's
   icon-only plus button, labelled and titled `Add to Blocks` or `Add to Blocked by`. Open the
   Project/task picker as a floating dropdown anchored to that button, without moving either
   relationship list. Fit it to the viewport, opening above the button when there is more room
   there, and scroll its contents as needed. Follow the anchor through scrolling and live updates;
   close if it leaves its visible scroll area. The dropdown uses the browser's
   [popover top layer](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/popover)
   to avoid clipping by Task detail's scroll containers. LiveView retains ownership of the forms,
   results, and errors; a separate ignored positioning marker owns browser lifecycle and geometry.
   Focus the search on opening. Escape or the close button dismisses only the picker and restores
   its trigger focus; clicking outside dismisses it while preserving the user's new focus.
2. Choose a Project, defaulting to the current one; then search its Tasks across all Lists. Split
   the trimmed query on Unicode whitespace, including non-breaking spaces. For each term, match when it is a literal substring of the
   Task ID rendered in decimal **or** the title compared case-insensitively. This OR is inclusive:
   a term may match both fields. Require every term to match, and allow different terms to match
   different fields. For example, `42 publish` matches Task 42 titled `Publish site`. Treat `%`
   and `_` as literal characters, not search wildcards. A blank query shows the first page of
   candidates. Return at most 20 results in stable title/ID order. When the entire query is an
   exact Task ID, put that Task first. Show ID, title, Project, and List path.
3. Exclude the selected Task from suggestions, but revalidate the explicitly selected ID on
   submission. A stale search result cannot bypass domain rules.
4. Offer add/remove controls in both groups. In **Blocks**, the selected Task is the blocker; in
   **Blocked by**, the chosen linked Task is the blocker. The LiveView handler passes the ordered
   blocker and blocked Task identities directly to the shared `Taskman.Tasks` add/remove use cases;
   those use cases reload and validate them. One explicit Remove click deletes the identified edge.
   Show a local error if removal fails; refresh both endpoints after success. Removal never changes
   status.

### Done warning in the autosaved form

1. Selecting Done first attempts the domain transition. If unresolved direct blockers require
   confirmation, leave persisted status unchanged, retain the Done draft, and list each blocker
   with title, status, priority, and Project.
2. Offer **Keep current status** and **Mark Done anyway**. The latter sends the sorted IDs displayed
   in the warning as a separate explicit event.
3. Under the graph lock, recompute unresolved direct blocker IDs. If every current ID was displayed
   and confirmed, persist Done when the set is unchanged, smaller, or empty. If a new unresolved ID
   was absent from the confirmation, reject the status write and replace the warning with the current
   blocker list for review.
4. Keep other form fields usable. Closing or navigating away discards the pending Done draft without
   confirming it. Choosing another status cancels the warning. Use existing autosave conflict
   reconciliation for ordinary concurrent Task-field changes. Accepting the latest persisted status
   or successfully retrying Done clears the warning. Resolved or removed blockers no longer appear
   on subsequent checks. If the final unresolved blocker resolves or is removed before departure,
   discard the warned Done draft while saving ordinary pending edits; never turn departure autosave
   into implicit Done confirmation. Explicit Task-form submission and movement retain ordinary
   flushing. Departure callers opt in with `discard_done?: true` on `Editing.flush/2`.

## API contract

All routes require existing API authentication. In
`/api/v1/projects/:project_id/tasks/:task_id`, `:project_id` identifies the Project that must own
the route's Task `:task_id`. For relationship reads it is the selected Task; for mutations it is
always the blocking Task. The blocked target Task may belong to another Project.

### Relationship routes

| Method and path | Meaning | Body / successful result |
| --- | --- | --- |
| `GET /api/v1/projects/:project_id/tasks/:task_id/blocking` | Read both directions. | No request body; `200` with `data.blocks` containing Tasks this Task blocks and `data.blocked_by` containing Tasks that block it. |
| `POST /api/v1/projects/:project_id/tasks/:task_id/blocks/:target_task_id` | Route Task blocks target. | No request body; `201` with `{"data": EDGE}` for the inserted directed edge. |
| `DELETE /api/v1/projects/:project_id/tasks/:task_id/blocks/:target_task_id` | Remove that outgoing edge. | No request body; `200` with `{"data": EDGE}` for the edge removed. |

Mutations require no body data. An explicit JSON `{}` is accepted as empty; a nonempty parsed body
is rejected with `400 invalid_request`.

Separate `blocked-by` mutation routes would let a client keep the blocked Task in the URL, but
would duplicate the same edge operation, validation, and command guidance. The blocker-first route
expresses either perspective by choosing the blocking Task as its source. Both mutations identify
the same link URI. `POST` retains creation semantics: a duplicate returns the validation error
below, whereas an idempotent `PUT` would imply a successful retry. [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.4)
defines `PUT` as idempotent and permits resource-specific processing with `POST`.

### Representations

Each linked Task summary has exactly `id`, `project_id`, `project_name`, `title`, `status`,
`priority`, and `location` in the existing Task location shape. `priority` is the Task's current
`none`, `low`, `medium`, `high`, or `urgent` value. For selected Task 42, a relationship read can
return:

```json
{
  "data": {
    "blocks": [],
    "blocked_by": [
      {
        "id": 12, "project_id": 7, "project_name": "Launch",
        "title": "Approve copy", "status": "in_review", "priority": "high",
        "location": {"kind": "list", "list_id": 3, "path": ["Review"]}
      }
    ]
  }
}
```

Both arrays are present even when empty; each direction follows the Project name, Task title,
then ID ordering above. `EDGE` has exactly `blocking_task` and `blocked_task`, each a linked Task
summary. Their nested `id` values identify the ordered pair; the stored foreign keys are not
repeated as top-level response fields. For `POST /api/v1/projects/7/tasks/12/blocks/42` with no
body, the `201` response body is:

```json
{
  "data": {
    "blocking_task": {
      "id": 12, "project_id": 7, "project_name": "Launch",
      "title": "Approve copy", "status": "in_review", "priority": "high",
      "location": {"kind": "list", "list_id": 3, "path": ["Review"]}
    },
    "blocked_task": {
      "id": 42, "project_id": 9, "project_name": "Website",
      "title": "Publish site", "status": "pending", "priority": "urgent",
      "location": {"kind": "project", "list_id": null, "path": []}
    }
  }
}
```

To add or remove Task 12 in Task 42's **Blocked by** group, an API or CLI client uses Task 12's
Project 7 route with Task 42 as the target. Task detail calls the context directly with the same
ordered pair. Browser candidate search is a presentation aid; API and CLI clients can get exact IDs
from existing Project and descendant Task listings.

### Errors

| Condition | Result |
| --- | --- |
| Invalid ID syntax, a nonempty mutation request body, or unsupported query key | `400 invalid_request`. |
| Missing URL Project, selected Task in that Project, target Task, or exact edge on removal | `404 not_found`. |
| Duplicate, self-link, cycle, or forbidden parent-to-child link | `422 validation_failed`, with `fields.target_task_id` and a cause-specific message. |
| Parent change that makes an existing link point from parent to direct child | Existing Task `422 validation_failed` shape with `fields.parent_task_id`; reject the whole PATCH. |
| Concurrent Task-field update | Existing `409 concurrent_update` result. |

### Done update and confirmation

`PATCH /api/v1/projects/:project_id/tasks/:task_id` remains the status route. A warned Done
transition without confirmation returns `409`:

```json
{
  "error": {
    "code": "unresolved_blockers",
    "message": "Review unresolved direct blockers before marking Done",
    "blockers": [
      {
        "id": 12, "project_id": 7, "project_name": "Launch",
        "title": "Approve copy", "status": "in_review", "priority": "high",
        "location": {"kind": "list", "list_id": 3, "path": ["Review"]}
      }
    ]
  }
}
```

Each blocker uses the endpoint summary shape above. Persist no field from that PATCH. A client may
send confirmed IDs on the first PATCH when it already knows the blockers, or on a retry after this
warning. No prior warning response or token is required:

```json
{"task":{"status":"done"},"confirmation":{"unresolved_blocker_ids":[12,15]}}
```

An API client may instead authorize the Done transition without listing IDs:

```json
{"task":{"status":"done"},"confirmation":{"force_done_with_unresolved_blockers":true}}
```

The force field must be literal JSON `true`. It skips the blocker warning and blocker-set comparison
even if links or blocker statuses change before the write. The server still serializes the status
write under the graph lock. A confirmed or forced update returns `200` with `{"data": TASK}`,
where `TASK` is the current Task representation: `id`, `project_id`, `list_id`, `parent_task_id`,
`title`, `description`, `status`, `priority`, `due_at`, and `location`. Its `status` is `done`; the
response has no blocker-warning field. This is a case-specific confirmation, not a general force
option.

Other editable Task fields may be included in either request; they remain atomic with status.
Confirmation IDs must be positive integers; repeated IDs are accepted. The API validates the parsed
list and removes duplicates before passing it to the Tasks context. Order does not matter. Under the graph lock,
let `S` be the set of submitted confirmed IDs and `C` the set of current unresolved direct blocker
IDs. Persist the whole PATCH when every ID in `C` is in `S`, **including when `C = S`**, subject to
ordinary validation and optimistic concurrency checks. For `S = [12, 15]`, all of `C = [12, 15]`,
`C = [12]`, and `C = []` succeed. If `C` contains any ID outside `S`, return the full current
blocker list in the same `409` shape and persist nothing; for example, `C = [12, 17]` requires a
fresh confirmation that includes 17.

The confirmation object is valid only when requesting Done and must contain exactly one of these
two fields. Empty confirmation, malformed IDs, a force value other than `true`, both
fields together, or confirmation with a non-Done status use `400 invalid_request`. A valid
confirmation never bypasses a concurrent Task conflict, invalid status, a mismatched
`:project_id`/`:task_id` pair, or another validation failure. A Task already Done can receive
ordinary updates without it.

## CLI, help, completion, and bundled skill

### Commands and output

| Command | Meaning |
| --- | --- |
| `taskman tasks blocking show --project PROJECT_ID TASK_ID` | Read both directions. |
| `taskman tasks blocks add|remove --project PROJECT_ID TASK_ID --target TARGET_TASK_ID` | `PROJECT_ID` owns the blocking `TASK_ID`; that Task blocks the target. |

IDs stay exact, and the blocker always comes first in a mutation. The CLI checks ID syntax, then
sends those IDs to the API; it uses the server result for duplicate, self-link, cycle,
parent-child direction, and missing-edge decisions. Add and remove call the corresponding `POST`
and `DELETE` link URI without a request body. Readable `show` has separate Blocks and Blocked by
sections with IDs, titles, statuses, priorities, Project as `ID: name`, and location. Mutations print both
endpoints and direction. For example, `tasks blocks add --project 7 12 --target 42` prints
`Task 12 blocks Task 42 (Website).` on stdout.
That same command adds to Task 42's **Blocked by** group. `tasks blocking show --project 9 42`
shows `BLOCKED BY` with `12`, `Approve copy`, `in_review`, `high`, `7: Launch`, and `Review`; its empty
`BLOCKS` group says `None`. `--json` prints only the API `data` envelope on stdout.

The CLI validates new API success/error shapes. Validation, not-found, and warning errors retain
domain-failure exit status 3; authentication and transport statuses stay as they are. Malformed
local IDs or an invalid confirmation option exit 2 before HTTP.

### Done confirmation and force override

- `tasks update --status done` without confirmation prints the API blocker warning on stderr,
  exits 3, and leaves the Task unchanged when unresolved direct blockers exist.
- `--confirm-unresolved-blockers 12,15` may be supplied on the first invocation if the person
  already knows the blockers, or on a retry after a warning. It sends those IDs in the API
  confirmation object. There is no implicit retry or interactive auto-accept.
- If every current blocker ID was confirmed, succeed when the set is unchanged, smaller, or empty.
  If any current blocker ID is absent from the confirmation, print the full refreshed blocker list
  and exit 3.
- `tasks update --status done --force-done-with-unresolved-blockers` sends
  `confirmation.force_done_with_unresolved_blockers: true`. It succeeds in one request despite
  unresolved or concurrently changing blockers, subject to ordinary update validation and
  concurrency checks. Success uses the existing `tasks update` readable or `--json` output and
  exit 0, without printing a blocker warning.
- Both options require `--status done` and are mutually exclusive. The ID option requires a
  nonempty comma-separated list of positive integers, without whitespace inside the list
  (for example, `12,15`). The CLI passes repeated IDs through; the API removes duplicates after
  validating the parsed request. Invalid combinations exit 2 before HTTP.

### Parity material

Leaf help describes the blocker-first direction, exact IDs, Done confirmation, and the case-specific
force option. The shared registry generates Bash/Fish command and option completion, including the
new flag. Examples and the version-matched bundled skill show how to add an incoming blocker by
naming the blocking Task first, inspect both endpoint Projects, verify a link, provide known
blocker IDs on a first Done request, and seek separate human authority for a lifecycle decision.
The skill describes the force flag as an explicit override of the Done warning that requires human
authorization; an agent does not infer that authority from an added or resolved edge. Onboarding
changes only if its basic command map needs the new capability.

## Deletion and migration

### Deletion interaction

This increment adds explicit link removal in Task detail, API, and CLI. The later safeguards slice
owns Task, List, and Project deletion. The edge foreign keys cascade on Task deletion so no link
dangles. Those deletion workflows must calculate and show all incident edge removals **before**
deletion. A Project warning must identify links removed from surviving Tasks in other Projects.
That slice owns recursive child choices and detailed confirmation. The
Task deletion transaction must acquire the graph lock and then removes incident links through the
foreign-key cascade. No orphan cleanup job or Task-status change is needed.

### Migration

Create the empty table and indexes with the normal generated migration. Existing Tasks need no
backfill or status rewrite; existing parent and Task migrations stay untouched. Rolling back drops
only the new table and its link data. The normal release migration must finish before new code
serves relationship operations.

## Verification and acceptance

| Layer | Evidence required during implementation |
| --- | --- |
| Migration | Constraints, indexes, both foreign keys, cross-Project edges, and cascade cleanup. |
| Context | Both edge perspectives; duplicate/self/cycle rejection including a cross-Project cycle; allowed child-to-parent and forbidden parent-to-child; parent change against an existing block; atomic failures, stable ordering, and scoped/missing IDs. |
| Concurrency | Opposite adds cannot form a cycle; link/reparent races cannot make a forbidden pair; Done/link/status races accept equal, diminished, and empty unresolved sets when all current IDs were confirmed, but a new unconfirmed ID blocks the whole write and returns the current set; force writes serialize while allowing blocker-set changes; existing Task optimistic conflicts remain. |
| Task detail UI | Add/remove and navigate within/across Projects; narrow and keyboard usable Related Tasks; direct unresolved blockers warn while resolved/indirect blockers do not; cancel keeps status; confirmed Done persists once with unchanged or diminished blockers, while new unconfirmed blockers refresh the warning; Task/Project/List invalidations reconcile open rows. |
| Candidate search | Project-first search across Lists; every whitespace term uses inclusive ID/title OR and terms combine with AND; case-insensitive titles and literal special characters; exact-ID priority, blank query, stable ordering, and bounded results. |
| API and CLI | Canonical blocker-first mutation route and command, including an incoming link from the blocked Task's perspective; both read perspectives; bodies, representations, errors, authentication, malformed/stale IDs, readable/JSON output, exits, help, Bash/Fish completion, skill bundle/install contents. |
| Done confirmation | Known IDs succeed on the first request or a retry when every current blocker ID was confirmed; newly unconfirmed IDs return the full warning and a warned multi-field PATCH changes no fields. One-request force succeeds with unresolved or changing blockers; invalid force value, mixed confirmation modes, and non-Done force fail. Neither mode bypasses ordinary conflicts or validation. |

Use focused LiveView tests for Task detail behavior. Add a browser test only for behavior that those
tests cannot verify, following the [development guide](../guides/development.md#verification-expectations);
inspect responsive layout directly. Then run `mix precommit` and an implementation-surface
terminology scan. Obtain independent review of graph and concurrency correctness.

Graph and concurrency correctness received independent specialist review. Related Tasks browser
coverage uses Chromium at desktop and 390px viewport widths for picker geometry, live row updates,
keyboard interaction, focus, dismissal, and submission. Initial narrow geometry inspection used a
same-origin iframe; this evidence does not establish behavior in native mobile browsers.

## Delivery

The completed [implementation plan](../archive/plans/2026-09-29-task-blocking-and-search.md)
is archived as the historical execution record for this capability and the companion
[global Task search](2026-09-29-global-task-search-design.md). Implementation is delivered in
[PR #23](https://github.com/Incanus3/taskman/pull/23) and operator accepted. Merge and deployment
require separate authorization.
