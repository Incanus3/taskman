# Task comments design

**Status:** Operator-approved design, implemented 2026-09-27.

## Purpose and current state

Taskman needs a durable place for people and their API clients to append progress notes, questions,
and review evidence to a Task. A reader should see the whole conversation when opening the Task
or inspecting it through the API or CLI. A comment records what someone said; it does not accept
work, change Task status, or serve as a generated audit event.

This design applies to the authenticated, shared PostgreSQL workspace. At baseline checkout
`66631743` on `Incanus3/taskman-comments-design`, Task detail has a truthful but empty Activity
rail and a separate empty Sessions rail. The form, Activity, and Sessions currently share one
scroll area at all widths. `Taskman.Tasks` owns Ecto Task persistence; `ProjectLive` owns the
URL-backed detail modal and field-level autosave; project-scoped PubSub invalidations
refresh open browser views. `/api/v1/projects/:project_id/tasks/:task_id` returns one Task without
comments. The Req-backed `taskman` CLI has `tasks show` and a registry that generates help and
Bash/Fish completions. Its source-bundled skill is `priv/taskman_cli_skill/SKILL.md`.

At that baseline, the product specification excluded activity attribution and said account deletion
left Project, List, and Task records untouched. This design makes a narrow exception for
explicit comments: a comment records the authenticated posting account and may carry a separate
display name. On account deletion, the account identity disappears from the live workspace while the
comment and any custom display name remain. It does not add attribution to Task edits or per-user
ownership/permissions. The product specification and domain glossary now record this exception.

## Evidence and design constraints

- The installed `br` 0.7.0 surface, verified in an isolated disposable workspace on 2026-09-26,
  appends and lists issue comments and includes them in `show`. Its comment record has ID, issue
  ID, author, text, and creation time. It offers no comment edit/delete, individual lookup, or
  pagination. The focused evidence is in the orchestration repository's
  `docs/research/taskman-br-capability-map.md` (section “Focused activity, comments, and notes
  reference”). Taskman uses the reference for append/read behavior, while its named-author,
  identity, and storage contracts are defined here.
- [MVP product specification](../product/mvp-spec.md), [domain model](../product/domain.md),
  [Task detail design](2026-08-24-task-detail-navigation-design.md),
  [API/CLI/skill contract](2026-08-29-api-cli-agent-skill-design.md),
  [live updates design](2026-09-01-live-task-updates-design.md), and
  [authenticated access design](2026-09-02-authenticated-hosted-access-design.md) constrain this
  addition. Their attribution exclusions have been reconciled with this comments design.
- `docs/guides/development.md` requires meaningful persisted UI operations to have API, CLI,
  help, completion, bundled skill, and focused verification parity. Web adapters use public
  context APIs rather than Repo queries. Accounts is the only Ash domain; comments belong in the
  existing Ecto Tasks context.

## Approved product behavior

An authenticated user can read and post comments on any Task in the shared workspace. An API key
acts as its owning user. Every comment records that posting account while it exists. Browser posts
display the account's current login email. API/CLI posts may optionally supply a custom author
name; they still record and display the posting account separately. Callers cannot supply an
account ID or creation time. A custom name is a display label, not delegated identity or authority.
All Task statuses, including Done and Will Not Do, permit comments. A successful post refreshes
the Task's `updated_at` to at least the comment time, treating it as explicit Task activity. It
does not change Task status, editable
fields, or `lock_version`, and never confirms completion. A rejected or rolled-back post changes
neither the Task nor its timestamp.

The browser and readable CLI render the author as `$login` when `display_name` is `null`, or
`$display_name ($login)` otherwise. Thus an unnamed comment uses the posting account's current
login email; a custom-named comment shows that name with the verified login in parentheses. After
account deletion, `login` becomes “Deleted user”, yielding either “Deleted user” or
“Research agent (Deleted user)”. No email snapshot is retained. An email change updates the login
label of earlier comments on the next read. All authenticated workspace users may read these
labels, including login emails.

Comments are append-only plain text. Submission trims leading and trailing whitespace from
`text`, then validates that the trimmed value is nonempty and has at most 10,000 Unicode grapheme
clusters. It preserves internal whitespace and line breaks. Taskman renders text as text, escaping
markup and preserving line breaks; it does not interpret Markdown or embed HTML. Creation returns
the normalized stored text. This increment excludes comment editing, individual comment lookup,
deletion, search, attachment, reaction, mention, transition comment, and generated Task-change
Activity entries. Only future Task deletion removes its comments as part of deleting that Task;
that deletion operation has its own separate safeguards.

The complete thread is ordered by `(created_at ASC, id ASC)`. The ID tie-breaker makes simultaneous
timestamps deterministic. Empty threads are explicit empty arrays in machine responses and a
truthful empty state in the browser. This increment has no comment pagination or per-comment
lookup. That keeps opt-in `show` and the thread consistent with the initial append-and-read use case;
large threads can become slow, so pagination requires a later explicit API/CLI/UI contract.

## Browser interaction

The Task detail side panel becomes one shared content area selected by two pill tabs, **Activity**
and **Sessions**, at every width. Activity is selected when a Task detail opens or the selected
Task changes. Tab selection stays with the same open Task through rerenders and comment arrivals;
it does not change the URL or persist after the detail closes. Only the selected panel is visible
and available to keyboard and assistive-technology navigation. The tabs expose accessible tablist,
tab, and tabpanel semantics with selected state and linked labels. Left/Right arrows wrap between
the tabs; Home/End select the first/last tab. Because both panels are already available, moving
focus among tabs activates them immediately. Tab moves into the selected panel or its first
focusable content. Retain `#task-activity` and `#task-sessions` as the respective panel IDs.
Follow the [WAI-ARIA tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/) for focus and
semantics.

Activity shows the comment thread and composer. Task creation, title updates, status changes, and
priority changes are intended later Activity entries; they are not recorded or synthesized in this
increment. Sessions shows its truthful empty state in the same content area, immediately below the
tabs rather than after the Activity content. No Session data capability is introduced here.

At the existing two-column breakpoint (80rem), this panel sits beside the form. The pill tab
headers stay visible at its top. Within Activity, only the comment listing scrolls: it grows with
its content until its allocated maximum height, then scrolls independently. The composer remains
visible below the listing, outside its scroll area. The panel reserves room for both the headers
and composer before allocating height to the listing. The Sessions panel occupies the same area
when selected, so long Activity content never pushes it down. The modal remains content-sized
until it reaches 1000px or its viewport margin limit, whichever is smaller. At narrower widths,
the shared tab panel follows Task detail in document order. Its tab headers, comment listing, and
composer move together in the main detail
scroll; the list has no independent overflow.
Each comment shows the formatted author from the rule above, local rendering of the stored UTC
timestamp, and full plain text. Activity's empty copy says “No comments yet.”

For the wide Activity comment listing, capture its scroll state immediately before a comment
refresh changes the rendered thread. It was following the bottom if its content did not overflow its
allocated area, or if the remaining distance to the bottom was at most two pixels. After the
refreshed comments render, keep it at the bottom if it was following, including when a short
thread now overflows. If the reader had scrolled up, preserve their scroll position; do not jump
to new comments. Apply this to local posts and notification-driven refreshes. Preserve the last
visible Activity scroll state when switching to Sessions. While Activity is hidden, comment
notifications refresh its data without measuring its hidden zero-size viewport, switching tabs, or
moving focus. On return to Activity, restore the saved position if the reader had scrolled up, or
follow the bottom if they had been following it. Scope this state to the selected Task; switching
Tasks does not carry it over. The narrow shared detail scroll does not jump on an incoming comment.
Other Task-detail updates do not force the Activity listing to the bottom.

The composer is a multiline form with a “Write your comment here” placeholder, an accessible
field name, and a Post comment action. The button shows a muted Ctrl+Enter hint; pressing that
shortcut in the textarea submits through the same form action, while plain Enter adds a line break.
Its draft is independent of the Task autosave form: typing never writes a comment. The form remains available
when Task field autosave is saving or conflicted. Empty/too-long validation appears beside the
composer. On a successful post, add the returned comment to the thread and clear the draft; the
new comment itself provides the success feedback. On failure, preserve the draft and show a
recoverable error. Disable duplicate submission while a post is in flight. Retain the draft while
the same Task detail stays open and unrelated Task/comment invalidations arrive. Switching to
Sessions keeps the composer draft and its leave warning active; it does not ask for draft-loss
confirmation because Task detail remains open. Switching back reveals the same draft. If Submit
comment and close fails while Sessions is selected, keep Task detail open and select Activity to
reveal the preserved draft and error, then focus the error or composer.

### Leaving Task detail with a draft

When the composer field is non-empty, a user-initiated departure from the current Task detail
opens a confirmation before the detail is hidden or its route/state is replaced. Non-empty means
the composer value after trimming whitespace is not `""`, using the same normalization as comment
submission. Whitespace-only input closes immediately without a confirmation. This guard covers
the modal Close button, backdrop click, Escape after any open hierarchy drawer or Move popover has
handled its own Escape, navigation to another Task or location, and browser Back/Forward within
Taskman. A route or filter change that keeps the same Task detail open preserves the draft and
does not ask. With a trimmed-empty field, ordinary departure behavior remains immediate.
For this bypass, both Project and Task IDs must match the open detail, and a List-level route must
name an existing List in that Project. The Project-level and valid List-level Task routes are
equivalent; query and filter changes remain part of the preserved route. A stale List, different
Project, or different Task destination goes through the departure guard.

The confirmation explains that the unposted comment will be lost and provides exactly three
actions:

1. **Go back** dismisses the confirmation and returns focus to Task detail, preserving the
   composer value and the original Task route.
2. **Submit comment and close** runs the existing Task-detail flush and then the normal comment
   create operation. It navigates to the originally requested destination only after both succeed.
   If Task flush, validation, authentication, or persistence fails, the detail stays open, the
   comment draft remains, and the relevant error is shown. A successful post appears once; the
   composer clears as the modal closes. Disable repeat submission while this action is pending.
3. **Close and discard draft** runs the existing Task-detail flush, then clears the comment draft
   and navigates to the originally requested destination. If Task flush fails, the detail remains
   open with the comment draft intact. Discarding a comment draft never discards unsaved Task
   field edits implicitly.

An ordinary departure with a trimmed-empty comment draft keeps the established Task flush rule:
valid Task edits are flushed, while an invalid dirty field may be discarded. The explicit
Submit comment and close and Close and discard draft actions require a clean Task flush; invalid
Task edits keep the detail open. If comment posting fails after that flush, hide the confirmation,
show Activity with its error or composer focused, and retain the first pending destination for a
later departure attempt while the draft remains. A later successful ordinary Activity post clears
that retained destination so a new draft follows the next requested route.

The pending destination includes its route and filter parameters and survives the confirmation;
Go back cancels it. A browser history attempt must restore the current Task URL without adding a
new history entry or changing the destination entry, so Back or Forward can retry that departure
after cancellation. The Task detail modal remains visible. Its current
shared modal component starts hiding on dismissal, so implementation must defer that hide for
guarded Task-detail departures without changing the immediate close behavior of unrelated modals.
The confirmation is keyboard accessible, puts initial focus on Go back, treats Escape within the
confirmation as Go back, and restores focus on cancellation. Backdrop clicks and further departure
attempts cannot bypass an open confirmation. All same-document ProjectLive departure paths use one
guard so a link or browser history action cannot bypass the choice.

For a full page reload, tab close, or any navigation that unloads the document, attach a
`beforeunload` handler only while the trimmed composer value is non-empty. Supported browsers may
show a generic leave warning, but the page cannot customize its text or offer Submit comment and
close there.
If the user stays, the draft remains; if they leave, it is lost. Browsers may omit this event in
some circumstances, especially on mobile, so this is a best-effort safeguard rather than durable
draft storage.
Remove the handler when the trimmed composer value becomes empty, a comment is successfully posted,
or the draft is explicitly discarded. See
[MDN's `beforeunload` guidance](https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event).

An open Task detail consumes comment-created notifications for its selected Task, whether the post
came from this browser, another browser, the API, or the CLI. On notification, refetch the ordered
thread and current Task metadata, then reset the displayed LiveView comment stream in that order.
Key comments by ID so the local create response and its broadcast cannot show the same comment
twice. Reconcile `updated_at` without changing the Task editor's draft, focus, autosave state,
route, or composer text. A comment-only timestamp change is not an editable-field conflict. A
notification for another Task does not refresh this thread. A deleted or no-longer-project-scoped
Task follows the existing Task detail recovery and not-found behavior;
comment submission must not revive or move it.

Tab selection is ephemeral Task-detail presentation state keyed to the selected Task; it changes
no Task, route, API, or persisted comment state. The browser owns scroll measurement and
restoration around LiveView stream updates and tab visibility changes. The inactive panel retains
its draft and comment state while hidden and cannot receive keyboard focus. Comment creation and
authorization remain in the server-owned Tasks and Accounts boundaries.

## Persistence and application boundaries

Add a `task_comments` table with a positive integer `id`, required `task_id` referencing Tasks,
nullable UUID `actor_user_id` referencing Users, nullable `author_name`, required `text`, and
required UTC `created_at` stored at whole-second precision. A null `author_name` means no custom
display name was supplied. When provided, `author_name` is trimmed before validating that it is
nonempty and has at most 80 Unicode grapheme clusters; the normalized plain-text value is stored.
`task_id` is indexed with `created_at, id` for ordered Task-scoped reads. The Task foreign key
cascades on deletion. The User foreign key sets `actor_user_id` to null on account deletion, so
account deletion remains possible while the text and custom name stay in the shared workspace.
Comments have no `updated_at` because no update operation exists. A database constraint rejects
zero-length text; application validation owns Unicode whitespace normalization, the grapheme
limit, and friendly field errors. The Tasks boundary rechecks the actor's active authenticated
identity for creation.
If account deletion wins a race with comment insertion, creation fails as `authentication_required`
without leaving an orphaned comment.

`Taskman.Tasks` exposes Task-scoped list and create use cases. It verifies that Project and Task
still match, and creation receives a verified active authenticated User from the browser or API
boundary. A focused `Taskman.Tasks.Comment` schema and comment persistence capability own the
ordered query and insert. The web layer never queries Repo directly. One transaction inserts the
comment and sets the still-scoped Task's `updated_at` to at least both its previous value and the
comment's `created_at`;
neither write survives if the other fails. Task `lock_version` does not advance. The update must
be monotonic under interleaved comments and direct Task edits: direct Task mutations must not
overwrite a later comment timestamp with an earlier Ecto-generated value. Use a database-side
timestamp comparison at the shared Task write boundary and verify both write orders. This is a
Task metadata write, not a Task field edit or a new Activity entry.

Every successful comment creation broadcasts after commit, including browser, API, and CLI posts.
The `Tasks.create_comment/4` use case must run outside any caller-owned Repo transaction; it
raises `ArgumentError` before writing if called inside one. Its own transaction commits before
the notification is published, avoiding a broadcast for an inner transaction whose caller later
rolls back. Supporting nested composition would require a notification boundary after the
outermost commit.
The PubSub comment-created event carries Project ID, Task ID, and comment ID on the existing Project
topic. Failed or rolled-back posts do not broadcast. The event is distinct from Task update events
so comment posting does not run editable-field autosave reconciliation or change Task versions.
Consumers refetch through the context; the event is not an authoritative comment payload. A
separate Task update notification for the timestamp alone is unnecessary; selected Task detail
refreshes its metadata in response
to the comment event.

For reads, the Tasks context obtains posting-account labels in one batch through a focused
Accounts read capability. The web layer does not join or query Users directly. This read exposes
only the current email needed for comment display and does not make comments an Ash resource.

Concurrent posts are independent inserts. Server IDs and timestamps determine their final order.
There is no implicit retry or deduplication contract for POST: a client that retries after an
unknown response may create another comment and should inspect the thread before retrying.

## JSON API

Both routes require the existing Bearer API key and resolve Project then Task within that Project.
Malformed IDs are `400 invalid_request`; missing, stale, or cross-Project resources are
`404 not_found`. Unauthenticated/disabled/revoked identities retain the existing `401
unauthorized` boundary.

| Method and path | Meaning | Success |
| --- | --- | --- |
| `GET /api/v1/projects/:project_id/tasks/:task_id/comments` | Return the entire ordered thread. | `200 {"data":[COMMENT,...]}` |
| `POST /api/v1/projects/:project_id/tasks/:task_id/comments` | Append one comment from the authenticated actor. | `201 {"data":COMMENT}` |
| `GET /api/v1/projects/:project_id/tasks/:task_id?include_comments=true` | Return the Task representation with its ordered `comments` array. | `200 {"data":{...TASK,"comments":[COMMENT,...]}}` |

Ordinary `GET` Task show omits the `comments` key and keeps its existing representation. Only the
literal `include_comments=true` adds the full thread; unsupported values return `400
invalid_request`. The dedicated comments list always returns the full thread.

### Create request

`POST` accepts one JSON object with exactly one top-level `comment` key. Its value is an object
with a required string `text` and an optional string `author_name`; these are the only accepted
comment input fields. For example:

```json
{"comment":{"text":"Review notes","author_name":"Research agent"}}
```

Omitting `author_name` stores no custom name and returns `author.display_name: null`; the view
renders `author.login` alone. An explicit `null` author name is invalid; omit the field to leave
the custom name unset. The server trims `text` and any supplied `author_name`, then validates
each trimmed value as nonempty and within its length limit before storing it. A missing envelope,
non-object `comment`, non-string `text` or `author_name`, or any extra top-level or comment field
(including `id`, `task_id`, `actor_user_id`, `author`, and `created_at`) returns `400
invalid_request` without insertion. A missing `text`, or a provided `text` or `author_name` that
is empty or over-limit after trimming, returns `422 validation_failed` with `error.fields.text` or
`error.fields.author_name`.

### Returned comment

`COMMENT` in the route table is a response object, not the accepted `POST` body. Each element of
the comments list, the `201` create response's `data`, and an opt-in Task show's `comments` array
has exactly this shape. A comment posted with a custom name is represented as:

```json
{
  "id": 123,
  "task_id": 42,
  "author": {
    "display_name": "Research agent",
    "login": "person@example.com"
  },
  "text": "Review notes",
  "created_at": "2026-09-26T12:34:56Z"
}
```

`id` and `task_id` are positive integer IDs. `author.display_name` is the custom name string or
`null`; `author.login` is the posting account's current login string or “Deleted user” after
account deletion. `text` is the normalized stored plain text. `created_at` is a server-generated
UTC ISO 8601 timestamp. The client cannot submit these response fields as an `author` object or
choose the posting account through the create request. `author_name` is an input field and
`actor_user_id` is an internal storage field; neither is a key in the returned comment.

For a comment without `author_name`, `author` is
`{"display_name":null,"login":"person@example.com"}` while the account exists, or
`{"display_name":null,"login":"Deleted user"}` after deletion. With a custom name,
`display_name` remains that name after account deletion. `display_name` is presentation text, not
an ownership or authorization token. Existing Task list, create, update, move, and hierarchy
representations do not embed comments. Comment list and create routes reject unsupported query
parameters, including pagination and search, as `400 invalid_request`.

## CLI, help, completions, and bundled skill

Add commands under the existing Task noun group:

```text
taskman tasks comments list --project PROJECT_ID TASK_ID [--json]
taskman tasks comments add --project PROJECT_ID TASK_ID --text TEXT [--author-name NAME] [--json]
taskman tasks show --project PROJECT_ID TASK_ID [--include-comments] [--json]
```

`add` requires one `--text`; optional `--author-name` supplies only the display name, while the
posting account always comes from the API key. An empty or whitespace-only value for either
`--text` or a supplied `--author-name` reaches the API's trim-then-validate contract. There is no
account-ID override, file/stdin option, comment-ID command, edit, or delete. The `--project` and
Task ID operands follow existing exact-ID rules. Readable `list` prints every comment's ID,
formatted author using the same `$login`/`$display_name ($login)` rule as the browser, timestamp,
and full text in order, with an explicit empty message. Readable `show` adds the same comment
section after Task fields only with
`--include-comments`; ordinary show remains unchanged. JSON mode preserves the API envelope:
list returns a `data` array, add a `data` comment, and opt-in show a `data` Task with `comments`.
The client validates comment shapes and timestamps before rendering; malformed successful
responses remain `invalid_response` with exit status 5.

The existing CLI error mapping applies: local malformed invocation exits 2; API 400/404/422
exits 3; connection failure exits 4; invalid/server response exits 5; missing/rejected/forbidden
authentication exits 7. Failed JSON envelopes go to stderr. Registry-driven top-level/group/leaf
help, Bash/Fish completions, and onboarding examples must discover the new commands,
`--text`, `--author-name`, and `--include-comments`.
The version-matched bundled `taskman-cli` skill explains how to inspect a Task/thread, append a
comment when the operator requests it, verify the result, and distinguish a comment from human
approval of a lifecycle transition. It explains that a custom author name never changes the
verified posting account and should truthfully identify its source. It must not teach comment
search or account impersonation.

## Scope trade-offs and rejected approaches

- A dedicated Task comment resource keeps the first increment small and makes append/read behavior
  explicit. A general activity log is deferred because it would need event coverage, attribution,
  retention, and semantics for Task edits and status changes that this increment does not decide.
- A mutable Task notes field would overwrite or merge concurrent writing and would not preserve
  individual author/time records, so it does not meet the intended conversation use case.
- A custom author name is supported only for API/CLI posts, with the authenticated account kept
  and displayed separately. This permits agent attribution without changing authorization. A
  name could resemble another person; the verified login in parentheses limits that ambiguity.
- Current-email account display with a null account ID on deletion avoids retaining an email
  snapshot. A custom name stays visible after deletion by operator choice, while defaulted
  comments read “Deleted user.” Comments are not an audit ledger.
- The User UUID remains an internal foreign key. No comment read exposes it: clients have no
  account-targeted comment operation, and the verified current login is sufficient for the agreed
  attribution display. This avoids adding a stable account identifier to the public contract;
  clients cannot correlate an account across login changes through comments alone.
- Full-thread reads in the Activity panel and dedicated list API/CLI keep the first contract
  simple. Task show requires opt-in embedding to avoid enlarging ordinary Task inspection. The
  cost of full reads remains unbounded per Task; later pagination needs an explicit contract.
- The Activity/Sessions tabs supersede the earlier Task-detail design's stacked sections for the
  comments increment. They keep Sessions at the top of the same panel regardless of Activity
  length. At wide widths, only the Activity comment listing scrolls; the pill headers and composer
  remain visible. Bottom-follow keeps new comments visible for a reader at the bottom while
  preserving the position of someone reading older comments. Narrow Task detail keeps one main
  scroll area to avoid a nested mobile scroll region.
- `updated_at` means the most recent direct Task mutation or successful comment. Refreshing it on
  comments makes the Task reflect discussion activity, while `lock_version` stays specific to
  editable Task-field concurrency. The cost is a coordinated Task-row write on every post and a
  requirement to preserve timestamp order under concurrent direct edits.

## Verification for implementation

Focused context and migration tests must establish trim-before-nonempty and trim-before-length
validation for both `text` and supplied `author_name`, stable `(created_at,id)` ordering, author
deletion behavior, Task-scope isolation, and Task deletion cascade. They must also establish that
successful posts refresh Task `updated_at` without changing `lock_version` or status, rejected
posts leave it unchanged, and neither ordering of a concurrent Task edit and comment regresses the
timestamp. Verify that a successful post broadcasts its Project, Task, and comment IDs only after
commit, while rejected or rolled-back posts broadcast nothing. API and LiveView tests must cover
API-key and browser-session actor derivation,
custom-name display and null-name login fallback with a separate posting account but no public
User UUID, Project/Task scope, payload rejection
including attempted account override, error envelopes, and opt-in show/list parity. LiveView tests
must cover posting, validation/draft retention, terminal Task status, cross-session arrival without
disrupting autosave, and absence of fabricated Task-change activity.
Focused context, API, and LiveView tests must cover post-commit notifications from browser and API
actors, fresh ordered comments without duplicates after a local post and its broadcast, unchanged
drafts, Activity as the default tab, accessible tab markup, same-Task selection retention, reset to
Activity on Task switch, and failed submission from Sessions revealing the error in Activity. While
Sessions is selected, incoming comments must update Activity without switching tabs. Browser tests
must cover the client-owned keyboard, focus, history, unload, responsive layout, and scroll behavior.
For the wide Activity listing, verify fixed visible tab headers and composer, listing-only scrolling,
bottom-follow when already at bottom and when a short listing first overflows, preservation when
reading above the bottom, and the saved follow/position state after switching away and back. Check
that an incoming comment does not move the narrow shared detail scroll. LiveView tests cover
unrelated Task notifications.
CLI tests must cover readable/JSON list/add/show, status mapping, malformed responses, help,
Bash/Fish completion, onboarding, and bundled skill examples. Browser inspection should check
long multiline text, keyboard use, narrow/wide layout, and safe rendering of HTML-looking text.
Focused LiveView tests must cover empty and whitespace-only draft immediate close and all three
confirmation actions, including failed Task flush, failed comment post, draft retention, and
destination preservation. Browser tests must exercise Close, backdrop, Escape with layered UI,
Task switching, Back/Forward, focus behavior, and the supported `beforeunload` warning only when
the trimmed draft is non-empty. Server-owned outcomes already established in LiveView tests need
no duplicate browser scenario.
Run the repository's `mix precommit` gate after implementation changes.

Before claiming implementation complete, update the product docs and documentation index as
needed, inspect implementation-facing text for planning terminology, and verify that no UI/API/
CLI/skill surface advertises comment search, edit/delete, or account impersonation. The operator
approved this design. A separate operator request is needed to begin planning; design approval
does not authorize implementation, deployment, publication, or a shared-state change.
