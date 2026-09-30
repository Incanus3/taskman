# Global Task jump search

**Status:** Implemented and operator accepted

**Date:** 2026-09-29

## Purpose and scope

Add a small, cross-Project Task search to the authenticated navigation. A person can find a Task
by ID or title and open its existing detail modal from any normal app page. This capability joins
the [blocking relationships increment](2026-09-27-task-blocking-relationships-design.md) because
its candidate picker uses the same matching rule. It is a Task navigation aid, not an advanced
filtering, full-text, or saved-search system.

The [MVP product specification](../product/mvp-spec.md) governs navigation and API/CLI parity.
The [roadmap](../planning/roadmap.md) places this companion capability alongside blocking links.
The [implementation plan](../archive/plans/2026-09-29-task-blocking-and-search.md) is implemented.

## Current implementation

| Area | Observed state |
| --- | --- |
| Header | `Layouts.app` renders the shared `GlobalTaskSearch` control between the Taskman home link and account menu on authenticated pages; narrow screens use a search button and panel. |
| Task navigation | `ProjectLive` owns shareable `/projects/:project_id[/lists/:list_id]/tasks/:task_id` routes. `LocationScope.backdrop/3` preserves a visible Project/List backdrop and otherwise chooses the Task's actual location. Status filters do not decide that route. |
| Search | Global search, the Project-first blocking picker, and parent selection share literal whitespace-term ID/title matching. Each caller retains its own scope, exclusions, blank-query behavior, and ordering. |
| Programmatic access | `GET /api/v1/tasks/search` and `taskman tasks search` search all Projects by default with an optional Project filter. Other Task commands remain Project-scoped. Help and Bash/Fish completion come from one registry. |
| Modal | Search remains mounted while Task creation, detail, or recovery modals are active, preserving navbar geometry. It is inert while covered. Task detail's comment-draft and dirty-field departure rules continue to govern navigation. |

The shared workspace has authenticated users but no per-user Project or Task ownership. Every
authenticated user can search the same Projects and Tasks. No new persisted data or migration is
needed for this read-only capability.

## Operator decisions

| Decision | Selected behavior |
| --- | --- |
| Delivery | Include a bounded global Task jump search in this branch's MVP increment, with its own design. |
| Placement | Center the search input horizontally and vertically in the whole navbar on wide screens. Administration follows Taskman on the left, using an icon below 768px and its text label from 768px upward; Account settings and Sign out use icons at every width on the right. All icon links have accessible labels and hover titles. |
| Results | Show a dropdown; selecting a result opens Task detail. No results page. |
| Result details | Show each Task's priority alongside its status and location; include priority in API and CLI search results. |
| Blank query | Show a prompt and no Task results. |
| Narrow screens | Below the navigation sidebar's `lg` breakpoint (1024px), show a search button that opens a full-width search panel. |
| Destination | Preserve the current Project/List backdrop when it can show the Task under the existing direct/Include child Lists rule. Otherwise use the Task's owning List or Project root. |
| Open modal | Keep the navbar search rendered while a Task modal covers it, preserving component lifecycle and navbar height. Make it unavailable to pointer and keyboard interaction until the modal closes. |
| Programmatic scope | API and CLI search all Projects by default and accept an optional Project ID filter. |
| Parent picker | Use the same whitespace-term ID/title matching as global search and the blocking picker. Preserve its existing Project scope, eligibility exclusions, blank-query results, and stable ordering. |

## Search contract

### Matching and ordering

- Search all Tasks in all Projects and all statuses, including Done and Will Not Do. List placement
  does not limit candidates.
- Trim the query and split it on Unicode whitespace, including non-breaking spaces. For each
  term, match when it is a literal substring
  of the Task ID rendered in decimal **or** of the title compared case-insensitively. This OR is
  inclusive, so a term may match both fields. Require every term to match; different terms may
  match different fields. `42 publish` matches Task 42 titled `Publish site`.
- Treat `%`, `_`, and other input as literal search characters, never SQL wildcard or regular
  expression syntax. Bind input as query parameters.
- A blank query produces no results. A nonblank query returns at most 20 distinct Tasks. If the
  entire trimmed query is an existing Task ID, put that exact Task first. Sort remaining results
  by case-insensitive title, then Project name, then Task ID. The same query has deterministic
  ordering across browser, API, and CLI.
- Each result includes Task ID, title, status, priority, Project ID/name, and owning List path
  (or Project root). The result does not include the description or comments.

The blocking picker applies this predicate within its chosen Project and excludes the selected
Task. Navbar search applies it across all Projects. API and CLI searches may optionally limit it
to one Project. Parent selection also uses the shared matcher within its existing Project scope
and still excludes the current Task and descendants. Each caller keeps its own blank-query
behavior and ordering: the parent picker shows up to 20 eligible Tasks on blank input and keeps
its existing stable Task order for non-exact matches. An eligible exact ID stays first. This
matching change supersedes only the title-only search rule in the delivered
[parent-child design](2026-08-30-parent-child-task-hierarchy-design.md); its selection and
parentage rules remain in force.

### Read behavior and performance

`Taskman.Tasks` exposes a global search use case backed by a focused `Taskman.Tasks.Search` query.
Filter and limit in PostgreSQL, join Projects for the returned Tasks, and batch-load Lists across
the returned Projects to build owning paths. Avoid loading every Task or issuing one query per
Project. Search is a committed-data read and does
not acquire the blocking graph advisory lock. A result is a navigation suggestion, not an
authorization or persistence decision; opening it resolves the Task and location again.

Use existing storage initially. Arbitrary substring matching can scan Tasks; keep the fixed result
limit and measure representative search latency during implementation. Add a search index only if
that evidence warrants its migration and maintenance cost.

A 2026-09-29 local measurement with 1,500 Tasks applied `LIMIT 20` in 1.625 ms and had a 3.406 ms
median context-call time, so no search index was added. Location projection loads the Lists in each
returned Project; this measurement did not cover Projects with very large List trees. Measure that
shape if it becomes representative before changing the projection or indexing strategy.

## Browser interaction

### Header and result panel

1. Center the input horizontally and vertically in the whole authenticated navbar on wide screens,
   with a 32rem maximum. Remove the shared input wrapper's bottom spacing within the search form.
   Account settings and Sign out use fixed-size icons at every width, with accessible labels and
   hover titles. Administration follows the Taskman home link in left application navigation,
   uses the wrench-screwdriver icon with an accessible label and hover title below `md` (768px),
   shows its text label from 768px upward, and remains administrator-only. Left navigation may wrap
   at narrow widths so both links remain readable without overlapping search. The email truncates to fit the
   remaining account space. Switch between desktop input and narrow Search button at the same
   `lg` breakpoint (1024px) used to collapse the navigation sidebar. Below that width, render a
   labelled Search button; activating it opens a full-width panel and focuses
   the input.
2. Keep suggestions hidden until the search is active. Before typing, show a short prompt such
   as `Search Tasks by ID or title`; show no Task rows.
   After input, show a loading state, up to 20 results, or `No Tasks found`. Show Project and List
   path in each row so same-titled Tasks remain distinguishable. Show each result's status and
   priority.
3. Support typing, arrow-key selection, Enter to open, Escape to close, pointer selection, and
   visible focus. Visibly highlight the keyboard-selected result according to `aria-selected`
   while focus stays in the input, on desktop and narrow screens.
   Scroll the selected result into view when arrow navigation moves it outside the visible result
   panel, while retaining input focus on both surfaces.
   The input has a label and accessible combobox/listbox relationships. Announce
   loading, empty, and error states. Clicking outside or moving focus outside the search controls
   closes the suggestions and narrow panel. Moving focus to a result or retry button keeps the
   search open. Escape closes search from any focused control in its surface, including results
   and retry buttons. Escape or the narrow close button restores focus to the narrow trigger; outside
   dismissal preserves the user's new focus. An open narrow panel must not expose desktop
   suggestions or disable desktop dismissal when the viewport widens.
4. A search failure leaves the current page unchanged and offers retry. Activating Retry by keyboard
   or pointer focuses the search input before requesting the same query. Successful results remain
   visible; a repeated failure keeps the error and query visible with input focus. Editing the query
   replaces earlier results. Closing or navigating clears the query and results; search history is
   not stored.
5. Existing Task modals cover the navbar. Keep the search component mounted so opening and closing
   creation, detail, or recovery modals does not change navbar height or recreate the search.
   Make the search inert while covered; the current custom modal backdrop blocks pointer access
   but does not itself remove background controls from keyboard focus. Do not raise the header
   above modals or add a second search control inside detail. Search becomes available after
   closing a modal through its existing departure flow. Ordinary search closing and navigation
   still clear its query and results.

### Opening a result

Resolve the selected Task and its current Project/List again before choosing a route. When the
current page has an active Project workspace, use `LocationScope.backdrop/3`: retain its selected
Project/List when that backdrop exists and the Task is visible there by direct or Include child
Lists scope. Status filters do not affect this choice. Otherwise open the Task over its current
owning List, or over Project root for a directly owned Task. From settings or another page without
a workspace backdrop, use the owning location. Cross-Project results always use their own Project.

The resulting URL is shareable and uses the existing Task detail modal. If the Task or its
location changes between result display and selection, resolve current authority and apply the
same route rule. If the Task was deleted, show the existing Task-not-found behavior instead of
opening a stale row. Selecting a result does not edit Task state.

## API and CLI contract

### JSON API

Add authenticated `GET /api/v1/tasks/search?q=QUERY[&project_id=PROJECT_ID]`. Without
`project_id`, it searches all Projects; with it, results belong only to that Project. `q` is
required and must contain a non-whitespace term. Missing or blank `q`, repeated parameters,
unsupported query keys, or a malformed/nonpositive `project_id` return `400 invalid_request`.
An unknown Project ID returns `404 not_found`. No match is a successful `200` with `{"data":[]}`.
Authentication failures retain existing API behavior. The API returns at most 20 results, without
pagination:

```json
{
  "data": [
    {
      "id": 42,
      "title": "Publish site",
      "status": "pending",
      "priority": "urgent",
      "project_id": 9,
      "project_name": "Website",
      "location": {"kind": "list", "list_id": 11, "path": ["Release"]}
    }
  ]
}
```

These are search summaries, not full Task representations. The endpoint accepts only `q` and
optional `project_id`; it does not accept status, sort, List, offset, or page filters. The fixed
ordering and cap keep the dropdown and programmatic results equivalent. Invalid requests use the
existing `{"error":{"code":"invalid_request","message":"Invalid request"}}` shape. Unauthenticated and
forbidden requests retain the existing `401 unauthorized` and `403 forbidden` shapes.
For a Task at Project root, `location` is
`{"kind":"project","list_id":null,"path":[]}`; a listed Task uses the existing `list` kind and
its nonempty owning List path as shown above.

### CLI, help, completion, and skill

Add `taskman tasks search QUERY [--project PROJECT_ID] [--json]`, where a multiword query is passed
as one quoted shell argument, for example `taskman tasks search "42 publish"` or
`taskman tasks search "publish" --project 9`. `--project` is optional and maps to the API
`project_id` filter. The CLI calls the new API endpoint; it does not search local data. Readable
output has columns `ID`, `TITLE`, `STATUS`, `PRIORITY`, `PROJECT`, and `LOCATION`, with one row per
result. `PROJECT` uses `ID: name` so an unfiltered result provides the exact Project ID needed by
follow-up commands.
An empty successful result prints `No Tasks found.` and exits 0. `--json` prints the existing
API-compatible `{"data":[...]}` envelope and no prose. Missing or blank QUERY is a local usage
error: exit 2 and no HTTP request. A malformed or nonpositive `--project` is also a local exit-2
error. An unknown Project follows the API's `404 not_found` domain-failure exit 3. Other API
errors retain existing authentication and transport exit conventions.

The Task command handler must dispatch search with an optional `--project` before resolving the
required `--project` argument for existing scoped Task commands. Other Task commands retain their
current Project requirement.

The command registry owns usage, examples, help, and Bash/Fish completion. The CLI response
validator accepts this exact search-summary shape. Update the bundled version-matched CLI skill
with a search example and a follow-up `taskman tasks show --project PROJECT_ID TASK_ID` example
using IDs from a result when full Task details are needed. Explain that search returns summaries,
while `tasks show` returns Task details. Search does not grant authority to change Task status or
relationships.

## Expected file boundaries

| Area | Responsibility |
| --- | --- |
| `lib/taskman/tasks/search.ex`, `lib/taskman/tasks.ex`, `lib/taskman/tasks/hierarchy.ex` | Shared query term parsing and matching, global/Project-scoped query functions, caller-specific blank behavior and ordering, summary projection, public use cases; adapt the existing parent-candidate query to the shared matcher. |
| `lib/taskman_web/components/layouts.ex`, `lib/taskman_web/components/global_task_search.ex` | App header placement and reusable, stateful search UI. |
| `lib/taskman_web/live/project_live/`, existing `LocationScope` and `Paths` | Active-workspace backdrop selection and Task route. Other authenticated pages navigate through the owning location. |
| `lib/taskman_web/controllers/api/`, `lib/taskman_web/router.ex` | Authenticated cross-Project search route, query validation, and JSON representation. |
| `lib/taskman/cli/`, `priv/taskman_cli_skill/SKILL.md` | HTTP command, output/shape validation, help, completion, and agent guidance. |
| Corresponding `test/` files | Query, navigation, API, CLI, and responsive UI contract coverage. |

The layout-wide interactive control needs one shared owner; a stateful LiveComponent is justified
because the header appears under multiple authenticated LiveViews. Keep the matching query in the
Tasks context so browser and API paths cannot diverge.

The operator chose an inline dropdown instead of a results page. Duplicating search state in each
page LiveView would make the app-wide control diverge; the shared component keeps one interaction
contract. A new search index is deferred pending observed latency rather than assumed scale.

## Verification and limits

| Layer | Evidence required during implementation |
| --- | --- |
| Query | Inclusive ID/title OR per term, AND across terms, case-insensitive titles, literal `%`/`_`, decimal ID substrings, blank query, exact-ID first, stable ties, all Projects/statuses, 20-result cap, and no per-Project query loop. Parent-candidate search uses the same matcher while retaining its Project and descendant exclusions, blank results, and ordering. |
| Search UI | Centered desktop input; narrow button/full-width panel; prompt, loading, no match, error/retry; result status, priority, and location; keyboard, pointer, focus, and screen-reader states; modal-covered header; no Task writes. |
| Navigation | Same-Project visible backdrop retained; invisible or cross-Project result goes to owning List/root; Include child Lists affects visibility, status filters do not; stale move/deletion resolves safely. |
| API/CLI | Authentication; all-Project default and optional Project filter, including invalid/missing Project; exact route/query/result/error contracts with priority; readable and JSON output; exit codes; registry help and Bash/Fish completion; installed skill content. |

Use focused LiveView tests for search rendering, events, and navigation. Add a browser test only
for behavior those tests cannot verify, following the
[development guide](../guides/development.md#verification-expectations); inspect responsive layout
directly. Then run `mix precommit` and an implementation-surface terminology scan.

Browser coverage uses Chromium at desktop and 390px viewport widths for keyboard selection,
scrolling, focus, Escape, Retry, outside dismissal, search DOM identity and constant navbar height
across modal open/close, and native interaction blocking while covered. Responsive geometry was also inspected
through a same-origin narrow iframe; neither method establishes native mobile-browser behavior.
The initial automated loading/focus coverage gap is partly closed by the later focus regressions;
computed loading visibility still relies on direct inspection. Renew browser inspection when
loading presentation, geometry, or focus behavior changes.

## Delivery

The completed [implementation plan](../archive/plans/2026-09-29-task-blocking-and-search.md)
is archived as the historical execution record for this capability and the companion
[blocking relationships](2026-09-27-task-blocking-relationships-design.md). Implementation is delivered in
[PR #23](https://github.com/Incanus3/taskman/pull/23) and operator accepted. Merge and deployment
require separate authorization.
