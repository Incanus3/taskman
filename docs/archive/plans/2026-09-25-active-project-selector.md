# Active Project Selector Implementation Plan

**Status:** Completed and archived

**Goal:** Make one Project the active sidebar context, give Projects editable identity metadata, keep Task-table filters across navigation, and provide explicit filter-bearing share links.

**Architecture:** Persist Project identity on `projects`; keep Project and Task queries in contexts. `ProjectLive` coordinates route changes, streams, and notifications, while socket-free edit and filter models own pure transitions. A Project-selector component owns the header/dropdown, `WorkspaceNavigation` owns the active Project's Lists-only tree, and separate browser hooks own Project memory and Task-table preferences. API and CLI use the same Project context contract.

**Tech stack:** Elixir/Phoenix 1.8, Ecto/PostgreSQL, LiveView/HEEx, daisyUI/Tailwind, JavaScript hooks, ExUnit/LiveViewTest, browser acceptance.

**Specification:** [Active Project Selector Design](../../specs/2026-09-22-active-project-selector-design.md). Read it in full before implementation; this plan is an execution map, not a replacement.

The unchecked checklist markers below preserve the approved execution contract; they do not
indicate remaining work. The repository-local Beads issues record delivery evidence.

## Global constraints

- No Project directory or machine-local checkout data; no Project deletion, pinning, or search.
- `description` is non-null, trimmed, may be `""`, maximum 160 characters; `icon` defaults to `"briefcase"` and is restricted to the eight keys in the specification; `color` defaults to `"#6366F1"`, accepts any six-digit hex color, and is stored uppercase.
- The dropdown only selects Projects. A separate header edit button and bottom-sidebar New Project button open the same modal. Keep the existing compact header's visual hierarchy.
- Explicit Project/List URLs win over remembered browser selection; `/` restores only the remembered Project's root *location*, while both Task-table filters independently restore from browser storage unless their respective URL parameters override them. Navbar **Taskman** always links to `/`.
- Normal application links omit `include_children` and `statuses`. Explicit query snapshots apply on load and Back/Forward; changing either filter persists it and removes both snapshot parameters from only the current history entry. Share copies both current values without changing the address bar.
- Navigation starts with a **Project tasks** row: its label links to the Project root, and an icon-only **Add root List** button sits at the trailing edge of that same row. They are separate click targets, so adding a List does not navigate. List icon classification uses direct Task presence, never filtered Task results.
- Preserve existing Missing List recovery, Task edit flushing, session-scoped List expansion, subscription behavior, and accessible keyboard/touch controls.
- Use context functions rather than Repo calls in web code. Do not introduce LiveComponents, dependencies, or dynamic icon names derived from unvalidated input.
- Follow repository guidance: test-first bounded increments, local commits after verification, `br` for Beads mutations, focused tests then `mix precommit`, responsive browser acceptance. Pushing/merging/deployment need separate approval.

## Review focus

The owning tasks below must pin these likely failure inputs, beyond the happy path:

1. A valid custom color entered in lowercase must survive an unrelated Project edit as uppercase without silently selecting a preset (Tasks 1 and 6).
2. A Project update notification arriving during a dirty local modal edit must preserve entered fields, while a pristine form refreshes from the canonical Project (Task 6).
3. A URL containing only one filter parameter must override only that preference, leaving the other mounted/stored value intact—including on Back and through a remembered-Project patch from `/` (Tasks 7–8).
4. A Task moved from one List to another must update both Lists' direct-Task icon classes even when the Task is hidden by the status filter (Task 4).
5. A Share click on a URL already containing stale filter parameters and an unrelated query/fragment must copy current filters, preserve unrelated parts, and leave the address bar untouched (Task 9).

## File boundaries and order

1. `projects/project.ex`, `projects.ex`, and `change_notifications.ex` own identity validation/persistence and Project event fields; a generated migration owns database defaults.
2. API controller/router/representation own HTTP shape and status codes; CLI registry/commands/presentation/client/bundled skill own the agent-facing mirror.
3. `tasks.ex` owns a batched direct-Task presence query; `lists.ex` and `lists/navigation_node.ex` own pure active-Project tree nodes.
4. `project_live/project_edit.ex` owns socket-free modal mode/form/target transitions. `components/core_components.ex` supports a static input prefix. `components/project_selector.ex` owns selected header and choices; `components/workspace_navigation.ex` owns Project-root row and Lists.
5. `project_live/workspace.ex` and `project_live.ex` coordinate selector/editor events, route restoration, scoped streams, and notifications.
6. `project_live/task_table_preferences.ex` owns server-side filter normalization; `tasks/listing.ex`, `project_live/paths.ex`, and Task route/recovery helpers consume it. Browser hooks in `assets/js/project_live_hooks.js` attach to the ProjectLive template; the preference hook replaces the status-only hook in `components/tasks/table.ex`, and the separate Project-memory hook waits for filter hydration before patching `/`.

Complete each task's red/green loop and commit before the next task. Test examples are contracts to implement and expand with the listed edge cases; keep tests behavioral rather than asserting decorative CSS. Use `mix help` before an unfamiliar Mix task.

### Task 1: Project identity, migration, and update notifications

**Files:** Generate `priv/repo/migrations/*_add_project_identity.exs` with `mix ecto.gen.migration add_project_identity`; modify `lib/taskman/projects/project.ex`, `lib/taskman/projects.ex`, `lib/taskman/change_notifications.ex`; test `test/taskman/projects/actions_test.exs` and a new focused migration test under `test/taskman/repo/migrations/`.

**Interfaces:** `Project.icons() :: [String.t()]`; `Projects.update_project(Project.t(), map()) :: {:ok, Project.t()} | {:error, Ecto.Changeset.t()}`; `ChangeNotifications.publish_project(project, :created | :updated, fields)`. Keep existing list/get/create/change interfaces.

- [ ] Write failing migration/context tests for backfilled database defaults and `NOT NULL`, empty/trimmed/overlong description, icon allowlist, lowercase/custom color normalization, create defaults, partial/full/no-op update, and exact created/updated notification fields. Include an invalid update that publishes nothing.

  ```elixir
  assert {:ok, project} = Projects.create_project(%{"name" => "  Alpha  "})
  assert {project.name, project.description, project.icon, project.color} ==
           {"Alpha", "", "briefcase", "#6366F1"}
  assert {:ok, updated} = Projects.update_project(project, %{"color" => "#a1b2c3"})
  assert updated.color == "#A1B2C3"
  assert {:ok, ^updated} = Projects.update_project(updated, %{})
  ```

- [ ] Run `mix test test/taskman/projects/actions_test.exs test/taskman/repo/migrations/`; confirm the new assertions fail for missing fields/update operation.
- [ ] Add the three migration columns with database defaults and `null: false`; schema defaults and changeset normalization; update via `Repo.update`; publish `:created` with initialized fields and `:updated` with deterministic `Ecto.Changeset.changed?/2`-derived persisted fields only. Guard no-op and invalid writes from publication.

  ```elixir
  add :description, :text, null: false, default: ""
  add :icon, :string, null: false, default: "briefcase"
  add :color, :string, null: false, default: "#6366F1"
  ```

  ```elixir
  fields = [:name, :description, :icon, :color]
  changed_fields = Enum.filter(fields, &Ecto.Changeset.changed?(changeset, &1))
  # Publish :updated only after Repo.update/1 succeeds and changed_fields is nonempty.
  ```

- [ ] Run the same focused tests and `mix format --check-formatted` for touched files; confirm green, including migration rollback/forward coverage.
- [ ] Commit this bounded increment locally with a Project-identity message.

### Task 2: Project API representation and PATCH

**Files:** Modify `lib/taskman_web/router.ex`, `lib/taskman_web/controllers/api/project_controller.ex`, `lib/taskman_web/controllers/api/representation.ex`, and if required `lib/taskman_web/controllers/api/params.ex`; test `test/taskman_web/controllers/api/project_controller_test.exs`.

**Interfaces:** `GET` list/show and `POST` include `id,name,description,icon,color`; `PATCH /api/v1/projects/:project_id` accepts a partial `project` object and returns the same representation. Empty valid object is a 200 no-op; malformed ID 400, absent ID 404, changeset errors 422. Unknown fields remain ignored.

- [ ] Add failing authenticated controller tests for full create/list/show/update representations, partial and empty update, unknown field, invalid color/icon/description, malformed ID, and missing Project. Example request:

  ```elixir
  conn = patch(conn, ~p"/api/v1/projects/#{project.id}", %{"project" => %{"description" => "Roadmap"}})
  assert %{"data" => %{"description" => "Roadmap", "icon" => "briefcase"}} = json_response(conn, 200)
  ```

- [ ] Run `mix test test/taskman_web/controllers/api/project_controller_test.exs`; confirm contract failures.
- [ ] Route PATCH through the existing authenticated API scope, resolve Project with the existing ID/error conventions, call `Projects.update_project/2`, and expand the single Project representation helper used by all responses. Do not accept directory fields.

  ```elixir
  def update(conn, %{"project_id" => project_id, "project" => attrs}) when is_map(attrs) do
    with {:ok, id} <- Params.positive_id(project_id),
         %Taskman.Projects.Project{} = project <- Projects.get_project(id),
         {:ok, updated} <- Projects.update_project(project, attrs) do
      json(conn, %{data: Representation.project(updated)})
    else
      nil -> {:error, :not_found}
      {:error, reason} -> {:error, reason}
    end
  end
  ```
- [ ] Rerun the controller test and adjacent API authentication tests; confirm exact envelopes and statuses.
- [ ] Commit the API increment locally.

### Task 3: CLI Project identity and update

**Files:** Modify `lib/taskman/cli/registry.ex`, `lib/taskman/cli/commands/projects.ex`, `lib/taskman/cli/client.ex`, `lib/taskman/cli/presentation/{output,help,completions}.ex` as needed, and `priv/taskman_cli_skill/SKILL.md`; test `test/taskman/cli/{commands/projects_test,client_test,end_to_end_test}.exs`, `test/taskman/cli/presentation/{output,help,completions}_test.exs`, and skill bundle tests.

**Interfaces:** `projects create --name NAME [--description TEXT] [--icon ICON] [--color '#RRGGBB']`; `projects update PROJECT_ID` with at least one of those four options; PATCH sends only supplied fields. JSON output preserves all API fields; readable output retains ID/name and adds metadata.

- [ ] Write failing parser/command tests for exact POST/PATCH method/path/body, missing update options, all allowed icon values, invalid icon, partial description clear (`--description ''`), readable/JSON output, malformed API Project response, help, Bash/Fish completions, and bundled skill text.

  ```elixir
  assert request.method == :patch
  assert request.path == "/api/v1/projects/#{project_id}"
  assert request.body == %{"project" => %{"description" => ""}}
  ```

- [ ] Run focused Project CLI, parser, presentation, and bundle tests; confirm failures identify missing options/command/response fields.
- [ ] Extend the declarative command registry and Project command implementation; reuse its argument/error conventions. Validate icon from `Project.icons/0` or the shared finite contract without turning user strings into atoms. Extend Project response validation to require all metadata; update help/completion and skill guidance together.

  ```elixir
  attrs =
    invocation.options
    |> Map.take([:name, :description, :icon, :color])
    |> Map.new(fn {key, value} -> {Atom.to_string(key), value} end)

  body = %{"project" => attrs}
  # :update uses :patch and "/api/v1/projects/#{project_id}"; :create uses :post.
  ```

  ```elixir
  constraints: [{:at_least_one, [:name, :description, :icon, :color]}]
  ```
- [ ] Rerun focused CLI tests plus `test/taskman/cli/entrypoint_test.exs`; inspect generated help and both completion shells through the existing test entrypoint.
- [ ] Commit the CLI increment locally.

### Task 4: Active-Project navigation model and semantic List icons

**Files:** Modify `lib/taskman/tasks.ex`, `lib/taskman/lists.ex`, `lib/taskman/lists/navigation_node.ex`, `lib/taskman_web/components/workspace_navigation.ex`, `lib/taskman_web/live/project_live/workspace.ex`, and `lib/taskman_web/live/project_live/reconciliation.ex`; test `test/taskman/lists/navigation_test.exs`, `test/taskman_web/components/workspace_navigation_test.exs`, and relevant `test/taskman_web/live/project_live/{lists,workspace_updates,task_updates}_test.exs`.

**Interfaces:** `Tasks.list_ids_with_direct_tasks(Project.t()) :: MapSet.t(pos_integer())`; make the pure navigation builder take one Project, its Lists, direct-Task ID set, selection, and expansion IDs, returning Lists-only nodes at root depth 1. Node fields distinguish leaf/child-only/mixed, and child-only expanded state. Project-root row is not a tree parent.

- [ ] Add failing tests for Project isolation, root depth, leaf empty/with Tasks, child-only closed/open, mixed with direct Tasks, descendant-only Tasks not making a parent mixed, and transition in both directions after moving a Task. Test the pure tree model before/after adding and removing the last child List, without adding a deletion operation. Include a status-hidden Task. Assert the **Project tasks** link and **Add root List** button occupy the same row but are separate controls; the button opens the form without navigating. `aria-current="page"` belongs on the Project tasks link for root-context Task routes but not missing-List routes.

  ```elixir
  assert Tasks.list_ids_with_direct_tasks(project) == MapSet.new([list_with_direct_task.id])
  assert %{kind: :list, depth: 1, icon: "hero-folder"} = root_node
  ```

- [ ] Run the focused context/component/LiveView tests; confirm the new model fails.
- [ ] Add one `select: task.list_id` query with `distinct` scoped to Project and non-null List IDs; derive child presence from the Project's Lists in the pure flattener. Stream only the active Project's nodes. In the component, render the **Project tasks** link and trailing **Add root List** button in the same first row, then root Lists directly below; remove Project tree nodes and the Lists heading. Use a single non-leaf disclosure button whose rest/hover/focus glyphs swap without layout shift, and no leaf chevron placeholder. Refresh navigation after local and external Task creation/movement and List creation, without changing Missing List recovery outcomes.

  ```elixir
  def list_ids_with_direct_tasks(%Project{id: project_id}) do
    from(task in Task,
      where: task.project_id == ^project_id and not is_nil(task.list_id),
      distinct: true,
      select: task.list_id
    )
    |> Repo.all()
    |> MapSet.new()
  end
  ```
- [ ] Rerun the focused tests. Inspect the resulting markup for `aria-level`, `aria-expanded`, independent links/buttons, keyboard focus, and no Repo calls in web code.
- [ ] Commit the navigation-model increment locally.

### Task 5: Reusable Project editor and prefixed hex input

**Files:** Create `lib/taskman_web/live/project_live/project_edit.ex`; modify `lib/taskman_web/components/core_components.ex`; add `test/taskman_web/live/project_live/project_edit_test.exs` and a focused core-component test if the prefix contract cannot be asserted through LiveView.

**Interfaces:** `ProjectEdit.empty/0`, `open_new/0`, `open_edit(Project.t())`, `validate(edit, attrs)`, `target(edit)`, `put_error(edit, changeset)`, `reconcile(edit, projects)`, and `canonical_color(six_digits)`; a core `<.input>` optional `prefix="#"` path wraps only the input in daisyUI's `.input` container, preserving existing label/error rendering. `ProjectEdit` never calls Repo or LiveView APIs.

- [ ] Add failing pure transition tests for closed/new/edit modes, title/submit label, six editable color digits versus stored `#RRGGBB`, valid custom lower-case value, preset filling, invalid/incomplete color preserving form values, stale edit target, pristine notification refresh, and dirty notification preservation. Add a regression test for ordinary `<.input>` without a prefix.

  ```elixir
  edit = ProjectEdit.open_edit(%Project{name: "Alpha", color: "#12ABEF"})
  assert edit.form[:color].value == "12ABEF"
  assert {:ok, "#12ABEF"} = ProjectEdit.canonical_color("12abef")
  ```

- [ ] Run the new tests; confirm missing module/prefix failures.
- [ ] Implement the socket-free editor state and canonical color conversion at its boundary; compose the daisyUI prefixed input so `#` is static text and `maxlength=6` applies only to editable characters. Keep schema validation authoritative and preserve supplied parameters on errors.

  ```elixir
  def canonical_color(<<digits::binary-size(6)>>) do
    if String.match?(digits, ~r/\A[0-9A-Fa-f]{6}\z/),
      do: {:ok, "#" <> String.upcase(digits)},
      else: {:error, :invalid_color}
  end

  def canonical_color(_digits), do: {:error, :invalid_color}
  ```
- [ ] Rerun editor/component tests and representative existing input/form tests.
- [ ] Commit the editor/input increment locally.

### Task 6: Project selector, unified modal, and reconciliation

**Files:** Create `lib/taskman_web/components/project_selector.ex`; modify `lib/taskman_web/live/project_live/{workspace.ex,reconciliation.ex}`, `lib/taskman_web/live/project_live.ex`, `lib/taskman_web/live/project_live.html.heex`; test `test/taskman_web/live/project_live/{project_live,workspace,workspace_updates,external_updates,autosave,recovery}_test.exs` and a new focused selector component test.

**Interfaces:** Workspace state gains `project_selector_open?` and `project_edit`; events open/close selector, open/create/edit/cancel/validate/save Project. Component accepts selected Project, Project stream, open state, and recovery inertness. A Project edit must retain route/List/Task state; create navigates to the new Project root.

- [ ] Write failing LiveView tests for neutral/selected header, omitted empty description, one-line truncation structure, separate edit/chevron IDs and labels, current choice, empty state, Escape/click-away, URL-backed selection, modal create/edit/validation, accessible icon radio choices, preset swatch selection, non-preset custom color preservation, stale target, failed save, and valid pending Task edit flush. Test pristine versus dirty form on external Project update and recovery-inert controls.

  ```elixir
  assert has_element?(view, "#project-selector-toggle[aria-expanded='false']")
  refute has_element?(view, "#project-edit-button")
  assert has_element?(view, "#new-project-button")
  ```

- [ ] Run the focused LiveView/component tests; confirm selector/modal failures.
- [ ] Render the header at the existing static Taskman location with the same compact tile/text proportions, a `min-w-0` truncating text region, edit button left of chevron, viewport-safe dropdown of Project links only, and accessible color/icon modal. Derive a readable light/dark icon foreground from the validated Project color; never interpolate unvalidated icon keys into `<.icon>`. Wire events through Workspace; re-fetch edit target before save and reconcile Project updates without resetting List/Task state. Use the existing modal/input/icon components and an accessible outside-click/Escape dismissal hook, not a LiveComponent. Ensure valid Task drafts flush through normal patch navigation.

  ```heex
  <button id="project-edit-button" type="button" phx-click="open_project_edit" aria-label="Edit Project">
    <.icon name="hero-pencil-square" class="size-4" />
  </button>
  <button id="project-selector-toggle" type="button" phx-click="toggle_project_selector"
          aria-expanded={to_string(@open?)} aria-label="Choose Project">
    <.icon name={if(@open?, do: "hero-chevron-up", else: "hero-chevron-down")} class="size-4" />
  </button>
  ```
- [ ] Rerun focused tests and inspect desktop/narrow selector/modal behavior and keyboard operation.
- [ ] Commit the selector/editor integration locally.

### Task 7: Browser-held Task filters and URL snapshot lifecycle

**Files:** Create `lib/taskman_web/live/project_live/task_table_preferences.ex`; modify `lib/taskman_web/live/project_live.ex`, `lib/taskman_web/live/project_live/workspace.ex`, `lib/taskman_web/live/project_live/tasks/listing.ex`, `lib/taskman_web/live/project_live/paths.ex`, Task detail/recovery route helpers that call Paths, `lib/taskman_web/components/tasks/table.ex`, and `lib/taskman_web/live/project_live.html.heex`; implement the browser hook in `assets/js/project_live_hooks.js`; test focused pure preference, paths, listing, route/recovery, and LiveView tests under `test/taskman_web/live/project_live/`.

**Interfaces:** Keys `taskman.task-table.include-children` (`"true"`/`"false"`) and `taskman.task-table.visible-statuses` (JSON array). Defaults: false and all Task statuses except `will_not_do`. `TaskTablePreferences` normalizes statuses in `Task.statuses/0` order. Initial hydration uses `hydrate_task_table_preferences` with `%{"include_children" => boolean(), "statuses" => [String.t()]}`; subsequent explicit route snapshots flow through the same hook owner. The LiveView exposes a `preferences_hydrated?` state after applying the initial snapshot. Absent query keys retain mounted preferences. User changes persist and replace the current history URL after deleting *both* filter keys only.

- [ ] Add failing pure/LiveView/browser tests for defaults, malformed/unavailable storage, `include_children=true` versus any other supplied value, empty/unknown/duplicate/reordered statuses, malformed non-string query parameters ignored, one-param override, initial load, Back/Forward, reload, Project/List/Task/root navigation, navbar `/` round trip, missing-List recovery, and preservation of unrelated URL query/fragment. On fresh `/` without Project memory, test saved filters and `/?include_children=false` or `/?statuses=` with conflicting saved values: each explicit parameter wins independently and the other preference remains intact. Assert normal links omit both filter parameters and an edited entry no longer reapplies its old snapshot.

  ```elixir
  assert Preferences.normalize_statuses(["done", "pending", "done", "bogus"]) == [:pending, :done]
  assert Paths.browse_path(project, list, true) == ~p"/projects/#{project.id}/lists/#{list.id}"
  ```

- [ ] Run focused tests and confirm URL/storage failures.
- [ ] Replace the status-only storage hook with one stable preference owner. On mount, choose each value from explicit URL, storage, default; persist accepted explicit values and send one hydration event, whose server handling sets `preferences_hydrated?` after applying both values. On LiveView route changes and `popstate`, apply only present explicit params and persist them; absence retains in-memory state. Guard duplicate hydration/route events and remove listeners on hook destruction. Make the include-child switch an event rather than a patch; status change uses the same persistence/URL-cleanup path. Use `history.replaceState` on the current entry after user changes, preserving unrelated query/fragment, and do not navigate. Remove filter query propagation from Paths and filter-state comparison from Task-route canonical checks; keep route identity and recovery behavior intact. Sorting and status-menu open state remain transient.

  ```javascript
  const clean = new URL(window.location.href)
  clean.searchParams.delete("include_children")
  clean.searchParams.delete("statuses")
  window.history.replaceState(window.history.state, "", clean.href)
  ```
- [ ] Rerun focused tests and browser-check Back/Forward through untouched and edited shared-link entries, then a reload of each.
- [ ] Commit the filter-persistence increment locally.

### Task 8: Remember active Project and restore `/`

**Files:** Modify `lib/taskman_web/live/project_live.ex`, `lib/taskman_web/live/project_live/workspace.ex`, and `lib/taskman_web/live/project_live.html.heex`; Project-memory hook in `assets/js/project_live_hooks.js`; inspect existing navbar link in the layout and keep it at `/`; test `test/taskman_web/live/project_live/{project_live,paths,recovery}_test.exs` and browser acceptance.

**Interfaces:** Browser key `taskman:selected-project-id:v1` stores a positive integer ID as a string. Connected `/` may send one `restore_remembered_project` event only after Task 7 has set `preferences_hydrated?`; the server validates the ID and `push_patch(replace: true)` to `/projects/:id`. The event reply distinguishes `accepted`, `stale`, and `ignored`; only a stale reply may clear storage, and then only if it still equals the submitted ID. Explicit Project/List routes persist only their validated Project ID and never restore over themselves.

- [ ] Add failing LiveView tests for valid/malformed/stale IDs, duplicate or delayed restore events, explicit Project/List precedence, valid Project with missing List, and navbar `/` round trip from a List without resetting either filter. Browser-test storage denied/throwing and reload persistence. Specifically test fresh `/` with both saved filters and a remembered Project, then `/?include_children=false` or `/?statuses=` with conflicting saved values: each explicit parameter wins independently and both resolved values survive the parameter-free root patch. A Project restoration attempted before preference hydration must be ignored or deferred, not lose the snapshot.

  ```elixir
  render_hook(view, "hydrate_task_table_preferences", %{"include_children" => true, "statuses" => ["pending"]})
  render_hook(view, "restore_remembered_project", %{"project_id" => Integer.to_string(project.id)})
  assert_patch(view, ~p"/projects/#{project.id}")
  ```

- [ ] Run focused tests; confirm no restoration behavior yet.
- [ ] Add a stable Project-memory hook element and client-side guards: write canonical ID only for valid Project-backed routes, read once on `/`, clear malformed/stale storage when possible, tolerate storage exceptions. Render the Task 7 hydration state as an attribute on that element; its `updated` callback offers the saved ID only when the attribute indicates hydration is complete. Server accepts restore only while action is `:index`, hydration is complete, and no recovery is active; it checks `Projects.get_project/1`, replies with accepted/stale/ignored, and patches with replacement. On a stale reply, remove the stored ID only if it still equals the submitted ID; an ignored or delayed reply must not erase a newer choice. Never restore a List or rewrite the navbar link.

  ```javascript
  if (this.el.dataset.preferencesHydrated === "true" && !this.restoreAttempted) {
    this.restoreAttempted = true
    this.pushEvent("restore_remembered_project", {project_id: savedId}, reply => {
      if (reply.status === "stale" && localStorage.getItem(storageKey) === savedId) {
        localStorage.removeItem(storageKey)
      }
    })
  }
  ```
- [ ] Rerun tests and browser-check reload, `/`, direct URL precedence, unavailable storage, and explicit `/` filter snapshots through the root patch.
- [ ] Commit the Project-memory increment locally.

### Task 9: Share snapshot and accessible feedback

**Files:** Modify `lib/taskman_web/live/project_live.html.heex` and the focused Share hook in `assets/js/project_live_hooks.js` (or a focused ProjectLive component if markup grows); test `test/taskman_web/live/project_live/{project_live,task_table,paths}_test.exs` plus browser acceptance.

**Interfaces:** Icon-only Share button immediately left of Include child Lists, present only for a valid Project Task view. It stays disabled until preference hydration. It copies the current absolute URL with `include_children=true|false` and `statuses=` followed by comma-separated statuses in Task order; replaces any stale filter params; preserves unrelated query and fragment; never changes the address bar. Clipboard failure exposes the generated URL for manual copy and accessible status feedback.

- [ ] Add failing LiveView/hook/browser tests for disabled-before-hydration, root/List/Task routes, empty statuses, stale filter params, unrelated query/fragment, no address-bar mutation, clipboard success and failure, and accessible feedback.

  ```javascript
  const url = new URL(window.location.href)
  url.searchParams.set("include_children", includeChildren ? "true" : "false")
  url.searchParams.set("statuses", statuses.join(","))
  // Clipboard receives url.href; window.history and location remain unchanged.
  ```

- [ ] Run focused tests and browser contract checks; confirm absent Share behavior.
- [ ] Add the button and hook; take current filter values from LiveView-rendered data, not existing URL params. Use the Clipboard API with a manual-copy fallback, reachable status text, and focus behavior that does not hide the generated link on failure.

  ```javascript
  const share = new URL(window.location.href)
  share.searchParams.set("include_children", this.el.dataset.includeChildren)
  share.searchParams.set("statuses", this.el.dataset.statuses)
  await navigator.clipboard.writeText(share.href)
  ```
- [ ] Rerun focused tests and browser-check the five Review Focus cases across relevant routes.
- [ ] Commit the Share increment locally.

### Task 10: Documentation, full verification, and handoff

**Files:** Update `docs/product/domain.md` and `docs/README.md` only where canonical behavior/status changes; update `docs/handoffs/improve-project-selection.md` and its index under the handoff rules. Do not rewrite historical API/CLI design. Verify all code/tests touched in Tasks 1–9.

- [ ] Run the focused suites from Tasks 1–9, then `mix precommit`; repair only regressions caused by this work, rerun failing checks, and record exact commands/results.
- [ ] Perform desktop and narrow browser acceptance for selector truncation/dropdown scrolling/dismissal, create/edit including custom hex, Project switching, Project memory, Project tasks/root List creation, three List icon modes and disclosure behavior, filter persistence, shared links, Back/Forward, clipboard failure, and recovery. Record residual uncertainty rather than claiming untested behavior.
- [ ] Search implementation-facing files for phase/ticket/planning terminology and Project-directory reintroduction; inspect API/CLI output and help. Update the canonical domain document with identity fields and add durable verification/remaining-state evidence to the handoff; maintain affected indexes.

  ```bash
  rg -n 'phase|milestone|bead|ticket|primary_directory|directory' lib test assets priv/taskman_cli_skill
  git diff --check
  ```

- [ ] Obtain the independent verification required by the execution approach. Inspect its findings, address confirmed issues with focused tests, and rerun affected checks. Do not expand into an unrequested full-branch review; the final gate is the implemented work and its relevant interactions.
- [ ] Commit the verified documentation/final fixes locally. Present any autonomous implementation rulings for operator acknowledgement. Do not retire the handoff, push, merge, or deploy without their distinct operator gates.

## Planning and execution gate

The implementation and verification were completed, and the operator accepted the workstream on
2026-09-26. All ten ordered child issues under epic `tas-active-project-selector-afnh` are closed.
The specification owns the lasting behavior; the Beads epic records delivery evidence.
