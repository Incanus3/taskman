# Task detail and navigation implementation plan

**Status:** Completed and archived on 2026-08-24

> Execute this plan task by task with a fresh review gate after each implementation task. Each
> implementation task begins with a failing focused test or failed acceptance observation, makes the
> smallest change that satisfies it, and ends with focused verification and a selective commit.

**Goal:** Turn the canonical Task editing modal into the responsive three-region MVP Task-detail
surface while preserving all existing URL, editing, autosave, lifecycle, and recovery behavior.

**Architecture:** `TaskmanWeb.ProjectLive` keeps ownership of routing, selected Task state, forms,
and persistence. A stateless `TaskmanWeb.TaskDetail` component composes the hierarchy, existing
`TaskForm`, Activity, and Sessions; an opt-in modal size supports the larger surface. A colocated
hook on the stable workspace root owns only the in-memory hierarchy preference and narrow-overlay
dismissal, while project CSS owns responsive geometry and scroll behavior.

**Tech stack:** Elixir 1.17+, Phoenix 1.8, LiveView 1.2, HEEx, Tailwind CSS 4, colocated LiveView
hooks, Ecto/PostgreSQL, ExUnit, LazyHTML, and Phoenix.LiveViewTest.

**Spec:** Read
[`docs/specs/2026-08-24-task-detail-navigation-design.md`](../../specs/2026-08-24-task-detail-navigation-design.md)
completely before implementation. Also preserve the established autosave contract in
[`docs/specs/2026-07-31-task-editing-lifecycle-design.md`](../../specs/2026-07-31-task-editing-lifecycle-design.md).

**Delivery work:**

- `tas-task-detail-navigation-fjb` — parent delivery feature.
- `tas-task-detail-navigation-fjb.1` — Task 1, wide modal contract.
- `tas-task-detail-navigation-fjb.2` — Task 2, semantic detail shell; blocked by `.1`.
- `tas-task-detail-navigation-fjb.3` — Task 3, responsive hierarchy; blocked by `.2`.
- `tas-task-detail-navigation-fjb.4` — Task 4, final verification and closure; blocked by `.3`.

## Global constraints

- Keep `/projects/:project_id/tasks/:task_id` as the sole canonical Task surface.
- Preserve the selected Project's direct Task list behind the modal.
- Preserve title, description, status, priority, due date-time, field-level autosave, dirty-field
  flushing, validation, persistence-failure, and not-found behavior.
- Add no schema, migration, context query, route, dependency, stateful LiveComponent, activity
  record, Agent Session record, or parent-child persistence.
- Render `No parent or child Tasks` beside the current Task's single hierarchy node.
- Render `No activity has been recorded for this Task.` and
  `No Agent Sessions are associated with this Task.` as separate empty states.
- Render no Launch, Attach, disabled Session action, fabricated activity, fabricated Session row,
  checklist, Related Tasks table, deletion action, or List behavior.
- Before an explicit toggle, empty hierarchy defaults collapsed and populated hierarchy defaults
  expanded.
- After any toggle or narrow-overlay dismissal, reuse that explicit state for every Task until the
  stable workspace LiveView is reloaded.
- Store the hierarchy preference only in the colocated hook instance; do not use server state,
  cookies, `localStorage`, or `sessionStorage`.
- At `xl` (`80rem`) and above, expanded hierarchy pushes the Task detail. Below `xl`, it overlays
  the detail with width `min(20rem, calc(100% - 3rem))`.
- Keep the modal content-sized up to its visible-viewport maximum; do not force viewport height.
- Give hierarchy its own vertical scroll while Task detail, Activity, and Sessions share one
  vertical scroll.
- On a narrow expanded hierarchy, outside click and first Escape collapse hierarchy without closing
  the modal; the next Escape closes the modal.
- Use project-owned HEEx components, the imported `<.icon>` and `<.input>` components, and the
  existing `TaskForm`; do not add raw inline scripts.
- Keep Repo calls, Ecto queries, schemas, and changesets out of `TaskmanWeb` presentation code.
- Do not assert colors, spacing, or responsive utility classes in LiveView tests.
- Use repository-approved version-control tooling and preserve unrelated handoff or workspace
  changes at every commit.
- PostgreSQL must be running before database-backed tests or `mix precommit` can pass.

---

## File map

- `lib/taskman_web/components/core_components.ex` — adds an opt-in `:wide` modal size while
  preserving the default compact modal contract.
- `test/taskman_web/components/core_components_test.exs` — verifies the modal's default and opt-in
  size semantics as a low-level component contract.
- `lib/taskman_web/components/task_detail.ex` — stateless composition of hierarchy, Task form,
  autosave feedback, Activity, and Sessions.
- `lib/taskman_web/live/project_live.html.heex` — uses the Task-detail component, keeps compact
  creation/not-found modals, and hosts the stable colocated hierarchy hook.
- `assets/css/app.css` — owns the hierarchy state geometry, `xl` breakpoint, overlay, scroll
  boundaries, and reduced-motion transition.
- `test/taskman_web/live/project_live_test.exs` — verifies semantic structure, truthful empty states,
  unavailable-action absence, canonical routing, and hook attachment.
- `test/taskman_web/live/project_live_autosave_test.exs` — remains the regression owner for
  navigation-safe edit persistence; change only if composition requires a selector update.
- `docs/planning/roadmap.md` — records slice 2 as complete only after all automated and browser
  acceptance gates pass.
- `docs/archive/plans/2026-08-24-task-detail-navigation.md`, `docs/handoffs/INDEX.md`, and
  `docs/handoffs/task-detail-navigation-design.md` — record execution evidence and retire resume
  state when the workstream is complete.
- `.beads/issues.jsonl` — exported repository-local delivery state; mutate only through `br`.

---

### Task 1: Add the opt-in wide modal contract

**Delivery issue:** `tas-task-detail-navigation-fjb.1`

**Files:**

- Modify: `lib/taskman_web/components/core_components.ex:127-175`
- Modify: `test/taskman_web/components/core_components_test.exs:1-25`

**Interfaces:**

- Consumes: existing `modal/1` assigns `id`, `show`, `on_cancel`, and `inner_block`.
- Produces:
  `size :: :default | :wide`, defaulting to `:default`.
- Guarantees: default modals keep `max-w-lg` and existing padding; wide modals expose
  `data-size="wide"`, use `max-w-7xl`, accept a content-sized viewport maximum, and leave internal
  padding to Task detail.

- [ ] **Step 0: Claim the delivery issue**

Run:

```text
br update tas-task-detail-navigation-fjb --status in_progress
br update tas-task-detail-navigation-fjb.1 --status in_progress
```

Expected: the parent feature and Task 1 issue are in progress; later child issues remain open and
blocked.

- [ ] **Step 1: Write the failing modal-size component test**

Extend `test/taskman_web/components/core_components_test.exs` with:

```elixir
test "modal keeps the compact default and exposes an opt-in wide size" do
  html = render_component(&sized_modals/1, %{})
  document = LazyHTML.from_fragment(html)

  default_modal = LazyHTML.query(document, "#default-modal-content[data-size='default']")
  wide_modal = LazyHTML.query(document, "#wide-modal-content[data-size='wide']")

  assert Enum.count(default_modal) == 1
  assert Enum.count(wide_modal) == 1
end

defp sized_modals(assigns) do
  ~H"""
  <CoreComponents.modal id="default-modal" on_cancel={JS.push("cancel-default")}>
    <h2 id="default-modal-title">Default modal</h2>
  </CoreComponents.modal>
  <CoreComponents.modal id="wide-modal" size={:wide} on_cancel={JS.push("cancel-wide")}>
    <h2 id="wide-modal-title">Wide modal</h2>
  </CoreComponents.modal>
  """
end
```

- [ ] **Step 2: Run the focused test and verify the missing size contract**

Run:

```text
mix test test/taskman_web/components/core_components_test.exs
```

Expected: the new test fails because neither modal content element renders the required
`data-size` value and `modal/1` has no declared `size` attribute.

- [ ] **Step 3: Implement the modal size attribute without changing default behavior**

Add the attribute beside the existing modal attributes:

```elixir
attr :id, :string, required: true
attr :show, :boolean, default: false
attr :on_cancel, JS, required: true
attr :size, :atom, values: [:default, :wide], default: :default
slot :inner_block, required: true
```

Replace the modal content element's single class string with:

```heex
<div
  id={"#{@id}-content"}
  data-size={@size}
  class={[
    "relative w-full overflow-hidden rounded-2xl bg-slate-900 text-left align-middle shadow-2xl shadow-black/50",
    @size == :default && "max-w-lg p-6 sm:p-7",
    @size == :wide &&
      "max-h-[calc(100dvh-2rem)] max-w-7xl sm:max-h-[calc(100dvh-3rem)]"
  ]}
  aria-labelledby={"#{@id}-title"}
  role="dialog"
  aria-modal="true"
  phx-click-away={hide_modal(@on_cancel, @id)}
  phx-window-keydown={hide_modal(@on_cancel, @id)}
  phx-key="escape"
  tabindex="-1"
>
```

Add `z-30` to the existing close button so it remains above the Task hierarchy's localized
overlay. Keep the backdrop, positioning wrapper, `show_modal/1`, and `hide_modal/2` unchanged. Do
not give `:wide` a fixed or minimum height.

- [ ] **Step 4: Run and format the focused component change**

Run:

```text
mix format lib/taskman_web/components/core_components.ex \
  test/taskman_web/components/core_components_test.exs
mix test test/taskman_web/components/core_components_test.exs
```

Expected: formatting exits 0 and every core-component test passes.

- [ ] **Step 5: Commit only the modal contract**

Inspect the workspace with repository-approved version-control tooling, select only
`lib/taskman_web/components/core_components.ex` and
`test/taskman_web/components/core_components_test.exs`, and commit them on the implementation
branch with:

```text
add wide Task detail modal size
```

Expected: the commit contains only the shared modal and its focused test. Design, plan, handoff, and
unrelated workspace files remain outside it.

- [ ] **Step 6: Close Task 1 after its review gate**

After the task's specification and quality reviews are clean, run:

```text
br close tas-task-detail-navigation-fjb.1 \
  --reason "Wide modal contract implemented, focused tests passed, and task review completed."
br show tas-task-detail-navigation-fjb.1 --json
br sync --status
```

Expected: `.1` is closed, `.2` is unblocked, and Beads reports healthy synchronized state.

---

### Task 2: Compose the semantic Task-detail shell

**Delivery issue:** `tas-task-detail-navigation-fjb.2`

**Files:**

- Create: `lib/taskman_web/components/task_detail.ex`
- Modify: `lib/taskman_web/live/project_live.html.heex:193-217`
- Modify: `test/taskman_web/live/project_live_test.exs:76-123`

**Interfaces:**

- Consumes:
  - `task :: %Taskman.Tasks.Task{}`
  - `form :: %Phoenix.HTML.Form{}`
  - `save_state :: :idle | :saving | :saved | :not_saved | :failed`
  - `save_message :: String.t()`
  - `cancel :: String.t()`
  - `has_hierarchy? :: boolean()`, default `false`
- Produces:
  `TaskmanWeb.TaskDetail.detail/1`, a stateless found-Task layout with the stable IDs defined in the
  design.
- Preserves: `TaskForm.form/1`, `autosave_task`, `submit_task_edit`, `#task-form`, and
  `#task-save-status`.

- [ ] **Step 0: Claim the unblocked delivery issue**

Run:

```text
br update tas-task-detail-navigation-fjb.2 --status in_progress
```

Expected: `.2` is in progress after `.1` has closed.

- [ ] **Step 1: Write the failing found-Task structure test**

Add a dedicated test after the existing canonical modal tests in
`test/taskman_web/live/project_live_test.exs`:

```elixir
test "found Task renders truthful detail regions without unavailable operations", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project, %{title: "Write launch checklist"})

  {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

  assert has_element?(view, "#task-modal-content[data-size='wide']")
  assert has_element?(view, "#task-detail-layout[data-has-hierarchy='false']")

  assert has_element?(
           view,
           "#task-hierarchy-toggle[aria-controls='task-hierarchy'][aria-expanded='false'][aria-label='Expand task hierarchy']"
         )

  assert has_element?(
           view,
           "#task-hierarchy [role='tree'] [role='treeitem'][aria-current='true']",
           "Write launch checklist"
         )

  assert has_element?(view, "#task-hierarchy-empty", "No parent or child Tasks")
  assert has_element?(view, "#task-activity-empty", "No activity has been recorded for this Task.")

  assert has_element?(
           view,
           "#task-sessions-empty",
           "No Agent Sessions are associated with this Task."
         )

  refute has_element?(view, "#task-sessions button")
  refute has_element?(view, "#task-detail-layout [data-session-row]")
end
```

Extend the existing invalid-Task URL test with:

```elixir
refute has_element?(view, "#task-detail-layout")
assert has_element?(view, "#task-modal-content[data-size='default']")
```

- [ ] **Step 2: Run the LiveView test and verify the missing shell**

Run:

```text
mix test test/taskman_web/live/project_live_test.exs
```

Expected: the new selectors fail because the found Task still renders the compact form-only modal.

- [ ] **Step 3: Create the stateless Task-detail component**

Create `lib/taskman_web/components/task_detail.ex` with this public boundary and structure:

```elixir
defmodule TaskmanWeb.TaskDetail do
  use TaskmanWeb, :html

  alias Taskman.Tasks.Task
  alias TaskmanWeb.TaskForm

  attr :task, Task, required: true
  attr :form, Phoenix.HTML.Form, required: true
  attr :save_state, :atom, required: true
  attr :save_message, :string, required: true
  attr :cancel, :string, required: true
  attr :has_hierarchy?, :boolean, default: false

  def detail(assigns) do
    ~H"""
    <div
      id="task-detail-layout"
      data-has-hierarchy={to_string(@has_hierarchy?)}
      data-hierarchy-expanded="false"
      class="task-detail-layout"
    >
      <div id="task-hierarchy-overlay" aria-hidden="true"></div>

      <aside
        id="task-hierarchy"
        aria-labelledby="task-hierarchy-title"
        class="task-hierarchy border-r border-slate-700 bg-slate-950/70"
      >
        <div class="flex items-center gap-2 p-3">
          <button
            id="task-hierarchy-toggle"
            type="button"
            aria-controls="task-hierarchy"
            aria-expanded="false"
            aria-label="Expand task hierarchy"
            class="grid size-9 shrink-0 place-items-center rounded-lg text-slate-300 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-4 focus:ring-indigo-400/20"
          >
            <span data-hierarchy-icon="expand">
              <.icon name="hero-chevron-right" class="size-4" />
            </span>
            <span data-hierarchy-icon="collapse" class="hidden">
              <.icon name="hero-chevron-left" class="size-4" />
            </span>
          </button>
          <h3
            id="task-hierarchy-title"
            class="task-hierarchy-content whitespace-nowrap text-sm font-semibold text-slate-100"
          >
            Task hierarchy
          </h3>
        </div>

        <div class="task-hierarchy-content px-3 pb-5">
          <ul role="tree" aria-label="Task hierarchy" class="border-l border-indigo-400/50 pl-3">
            <li
              role="treeitem"
              aria-current="true"
              class="rounded-lg bg-indigo-400/10 px-3 py-2 text-sm font-medium text-indigo-100"
            >
              {@task.title}
            </li>
          </ul>
          <p id="task-hierarchy-empty" class="mt-4 text-xs leading-5 text-slate-400">
            No parent or child Tasks
          </p>
        </div>
      </aside>

      <div id="task-detail-content" class="task-detail-content">
        <div class="task-detail-columns">
          <section class="min-w-0 p-6 sm:p-7" aria-labelledby="task-modal-title">
            <h2
              id="task-modal-title"
              class="pr-10 text-xl font-semibold tracking-tight text-slate-100"
            >
              Task
            </h2>
            <TaskForm.form
              form={@form}
              mode={:edit}
              change="autosave_task"
              submit="submit_task_edit"
              cancel={@cancel}
            />
            <p
              id="task-save-status"
              aria-live="polite"
              data-state={@save_state}
              class="mt-4 text-right text-sm text-slate-400"
            >
              {@save_message}
            </p>
          </section>

          <aside
            aria-label="Task activity and sessions"
            class="border-t border-slate-700 bg-slate-950/35 p-6 xl:border-l xl:border-t-0"
          >
            <section id="task-activity" aria-labelledby="task-activity-title">
              <h3
                id="task-activity-title"
                class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-300"
              >
                Activity
              </h3>
              <p
                id="task-activity-empty"
                class="mt-3 rounded-xl border border-dashed border-slate-700 p-4 text-sm leading-6 text-slate-400"
              >
                No activity has been recorded for this Task.
              </p>
            </section>

            <section
              id="task-sessions"
              aria-labelledby="task-sessions-title"
              class="mt-8 border-t border-slate-700 pt-6"
            >
              <h3
                id="task-sessions-title"
                class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-300"
              >
                Sessions
              </h3>
              <p
                id="task-sessions-empty"
                class="mt-3 rounded-xl border border-dashed border-slate-700 p-4 text-sm leading-6 text-slate-400"
              >
                No Agent Sessions are associated with this Task.
              </p>
            </section>
          </aside>
        </div>
      </div>
    </div>
    """
  end
end
```

Keep the module focused on composition. Do not add event handlers, Repo calls, Ecto queries,
hierarchy structs, or Session structs.

- [ ] **Step 4: Replace only the found-Task modal body**

In `lib/taskman_web/live/project_live.html.heex`, add `size={:wide}` only to the modal guarded by
`@selected_task`, then replace its heading, form, and save-status paragraph with:

```heex
<TaskmanWeb.TaskDetail.detail
  task={@selected_task}
  form={@task_form}
  save_state={@task_save_state}
  save_message={task_save_message(@task_save_state)}
  cancel={~p"/projects/#{@selected_project.id}"}
  has_hierarchy?={false}
/>
```

Do not change the new-Task modal, not-found modal, `ProjectLive` route handling, or
`task_save_message/1`.

- [ ] **Step 5: Run the structural test and inspect only the semantic selectors**

Run:

```text
mix format lib/taskman_web/components/task_detail.ex \
  lib/taskman_web/live/project_live.html.heex \
  test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/live/project_live_test.exs
```

Expected: every ProjectLive test passes. If a selector fails, inspect the relevant subtree with
`LazyHTML.filter/2`; do not dump or assert the full rendered document.

- [ ] **Step 6: Run the existing autosave regression suite**

Run:

```text
mix test test/taskman_web/live/project_live_autosave_test.exs
```

Expected: all autosave tests pass without modifying `ProjectLive` state or persistence logic. If a
selector changed only because of the new component wrapper, update the smallest selector and retain
the same behavioral assertion.

- [ ] **Step 7: Commit only the semantic detail shell**

Inspect the workspace, select only:

```text
lib/taskman_web/components/task_detail.ex
lib/taskman_web/live/project_live.html.heex
test/taskman_web/live/project_live_test.exs
test/taskman_web/live/project_live_autosave_test.exs
```

Omit `project_live_autosave_test.exs` when it did not need a change. Commit with:

```text
add Task detail regions and empty states
```

Expected: the commit contains the stateless shell and its behavioral tests, with no client hook,
responsive CSS, tracker, handoff, or unrelated change.

- [ ] **Step 8: Close Task 2 after its review gate**

After the task's specification and quality reviews are clean, run:

```text
br close tas-task-detail-navigation-fjb.2 \
  --reason "Semantic Task detail shell implemented, regressions passed, and task review completed."
br show tas-task-detail-navigation-fjb.2 --json
br sync --status
```

Expected: `.2` is closed, `.3` is unblocked, and Beads remains synchronized.

---

### Task 3: Add responsive hierarchy state and interaction

**Delivery issue:** `tas-task-detail-navigation-fjb.3`

**Files:**

- Modify: `lib/taskman_web/live/project_live.html.heex:1-247`
- Modify: `assets/css/app.css:94-108`
- Modify: `test/taskman_web/live/project_live_test.exs`

**Interfaces:**

- Consumes:
  - stable `#taskman-workspace`;
  - `#task-detail-layout[data-has-hierarchy]`;
  - `#task-hierarchy-toggle`;
  - `#task-hierarchy-overlay`.
- Produces:
  - colocated hook `.TaskDetailLayout`;
  - `data-hierarchy-expanded="true" | "false"`;
  - hook-instance preference `null | boolean`;
  - synchronized `aria-expanded`, accessible label, and expand/collapse icons.
- Guarantees: no LiveView event, browser storage, domain write, or duplicate form state.

- [ ] **Step 0: Claim the unblocked delivery issue**

Run:

```text
br update tas-task-detail-navigation-fjb.3 --status in_progress
```

Expected: `.3` is in progress after `.2` has closed.

- [ ] **Step 1: Write the failing stable-hook contract assertion**

In the found-Task detail test, add:

```elixir
assert has_element?(
  view,
  "#taskman-workspace[phx-hook='TaskmanWeb.ProjectLive.TaskDetailLayout']"
)
```

Keep the existing initial `aria-expanded="false"` and `data-has-hierarchy="false"` assertions.

- [ ] **Step 2: Run the focused test and verify the hook is absent**

Run:

```text
mix test test/taskman_web/live/project_live_test.exs
```

Expected: the new `phx-hook` selector fails while all Task-detail structure assertions continue to
pass.

- [ ] **Step 3: Attach the colocated hook to the stable workspace**

Change the opening workspace element to:

```heex
<div
  id="taskman-workspace"
  phx-hook=".TaskDetailLayout"
  class="min-h-screen bg-slate-950 text-slate-100"
>
```

Add this colocated script immediately before that workspace element's closing tag:

```heex
<script :type={Phoenix.LiveView.ColocatedHook} name=".TaskDetailLayout">
  export default {
    mounted() {
      this.hierarchyPreference = null

      this.onWorkspaceClick = event => {
        const toggle = event.target.closest("#task-hierarchy-toggle")

        if (toggle && this.el.contains(toggle)) {
          const expanded = this.currentExpanded()
          this.rememberAndApply(!expanded)
          return
        }

        const overlay = event.target.closest("#task-hierarchy-overlay")

        if (overlay && this.el.contains(overlay) && this.narrowOverlayExpanded()) {
          this.rememberAndApply(false)
        }
      }

      this.onWorkspaceKeydown = event => {
        if (event.key === "Escape" && this.narrowOverlayExpanded()) {
          event.preventDefault()
          event.stopPropagation()
          this.rememberAndApply(false)
        }
      }

      this.el.addEventListener("click", this.onWorkspaceClick)
      this.el.addEventListener("keydown", this.onWorkspaceKeydown)
      this.syncHierarchy()
    },

    updated() {
      this.syncHierarchy()
    },

    destroyed() {
      this.el.removeEventListener("click", this.onWorkspaceClick)
      this.el.removeEventListener("keydown", this.onWorkspaceKeydown)
    },

    layout() {
      return this.el.querySelector("#task-detail-layout")
    },

    currentExpanded() {
      return this.layout()?.dataset.hierarchyExpanded === "true"
    },

    narrowOverlayExpanded() {
      return !window.matchMedia("(min-width: 80rem)").matches && this.currentExpanded()
    },

    rememberAndApply(expanded) {
      this.hierarchyPreference = expanded
      this.applyExpanded(expanded)
    },

    syncHierarchy() {
      const layout = this.layout()
      if (!layout) return

      const contextualDefault = layout.dataset.hasHierarchy === "true"
      this.applyExpanded(this.hierarchyPreference ?? contextualDefault)
    },

    applyExpanded(expanded) {
      const layout = this.layout()
      if (!layout) return

      layout.dataset.hierarchyExpanded = String(expanded)

      const toggle = layout.querySelector("#task-hierarchy-toggle")
      if (!toggle) return

      toggle.setAttribute("aria-expanded", String(expanded))
      toggle.setAttribute(
        "aria-label",
        expanded ? "Collapse task hierarchy" : "Expand task hierarchy"
      )

      layout
        .querySelector("[data-hierarchy-icon='expand']")
        ?.classList.toggle("hidden", expanded)
      layout
        .querySelector("[data-hierarchy-icon='collapse']")
        ?.classList.toggle("hidden", !expanded)
    }
  }
</script>
```

The keydown listener stays on the workspace rather than `window`, so it can stop the narrow
overlay's Escape while the event bubbles from focused modal content and before the existing
window-level modal handler receives it.

- [ ] **Step 4: Add the focused responsive and scroll CSS**

Append these project-owned rules to `assets/css/app.css` without `@apply`:

```css
#task-detail-layout {
  position: relative;
  display: grid;
  grid-template-columns: 3rem minmax(0, 1fr);
  max-height: calc(100dvh - 2rem);
  min-height: 0;
  overflow: hidden;
  transition: grid-template-columns 160ms ease;
}

#task-hierarchy {
  position: absolute;
  inset: 0 auto 0 0;
  z-index: 20;
  width: 3rem;
  min-height: 0;
  overflow-y: auto;
  transition: width 160ms ease;
}

#task-detail-content {
  grid-column: 2;
  min-width: 0;
  min-height: 0;
  max-height: inherit;
  overflow-y: auto;
}

.task-detail-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

#task-hierarchy-overlay {
  display: none;
}

#task-detail-layout[data-hierarchy-expanded="false"] .task-hierarchy-content {
  display: none;
}

#task-detail-layout[data-hierarchy-expanded="true"] #task-hierarchy {
  width: min(20rem, calc(100% - 3rem));
}

#task-detail-layout[data-hierarchy-expanded="true"] #task-hierarchy-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: block;
  background: rgb(2 6 23 / 0.48);
}

@media (min-width: 80rem) {
  #task-hierarchy {
    position: relative;
    inset: auto;
    width: auto;
  }

  #task-detail-layout[data-hierarchy-expanded="true"] {
    grid-template-columns: 15rem minmax(0, 1fr);
  }

  #task-detail-layout[data-hierarchy-expanded="true"] #task-hierarchy {
    width: auto;
  }

  #task-detail-layout[data-hierarchy-expanded="true"] #task-hierarchy-overlay {
    display: none;
  }

  .task-detail-columns {
    grid-template-columns: minmax(0, 1fr) 18rem;
  }
}

@media (min-width: 40rem) {
  #task-detail-layout {
    max-height: calc(100dvh - 3rem);
  }
}

@media (prefers-reduced-motion: reduce) {
  #task-detail-layout,
  #task-hierarchy {
    transition: none;
  }
}
```

Keep all reusable controls and surfaces styled in the HEEx component. These CSS rules exist only
for stateful geometry, responsive reflow, scrolling, and reduced motion.

- [ ] **Step 5: Run formatting, focused tests, and asset compilation**

Run:

```text
mix format lib/taskman_web/live/project_live.html.heex \
  test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/components/core_components_test.exs \
  test/taskman_web/live/project_live_test.exs \
  test/taskman_web/live/project_live_autosave_test.exs
mix assets.build
```

Expected: all focused tests pass, Tailwind discovers the HEEx classes, and colocated JavaScript plus
CSS compile successfully.

- [ ] **Step 6: Verify the hierarchy interaction in a running browser**

With PostgreSQL running, start the application with:

```text
mix phx.server
```

Create or use one Project with two Tasks. At a viewport at least `1280px` wide:

1. Open the first Task and confirm the empty hierarchy starts collapsed.
2. Expand it and confirm the current Task node and empty message appear.
3. Confirm the hierarchy pushes detail while Activity and Sessions remain to the right.
4. Close the Task, open the second Task, and confirm the expanded preference remains.
5. Collapse, switch Tasks again, and confirm the collapsed preference remains.
6. Reload and confirm the empty hierarchy returns to collapsed.

Below `1280px` and at a representative phone width:

1. Expand hierarchy and confirm it overlays rather than narrows the form.
2. Confirm the drawer leaves a visible outside-dismissal target.
3. Click the localized overlay and confirm hierarchy collapses while the Task modal remains.
4. Expand again, focus a control inside the modal, and press Escape.
5. Confirm the first Escape collapses hierarchy and the second closes the modal.
6. Confirm Activity then Sessions render below the form.
7. Add enough browser zoom or temporary content inspection to confirm hierarchy scrolls separately
   while Task detail, Activity, and Sessions share their main scroll.
8. Confirm the modal remains content-sized when short and never exceeds the visible viewport.

Expected: every observation matches the approved specification with no clipped form controls,
hidden close action, focus loss, or horizontal page scrolling.

- [ ] **Step 7: Commit the responsive hierarchy behavior**

Inspect and commit only:

```text
lib/taskman_web/live/project_live.html.heex
assets/css/app.css
test/taskman_web/live/project_live_test.exs
```

Use commit message:

```text
add responsive Task hierarchy interaction
```

Expected: the commit contains the hook, responsive geometry, and stable hook contract test; no
domain, tracker, roadmap, handoff, or unrelated files are included.

- [ ] **Step 8: Close Task 3 after its review gate**

After the task's specification and quality reviews are clean, run:

```text
br close tas-task-detail-navigation-fjb.3 \
  --reason "Responsive hierarchy interaction implemented, browser acceptance passed, and task review completed."
br show tas-task-detail-navigation-fjb.3 --json
br sync --status
```

Expected: `.3` is closed, `.4` is unblocked, and Beads remains synchronized.

---

### Task 4: Verify the complete increment and close durable state

**Delivery issue:** `tas-task-detail-navigation-fjb.4`

**Files:**

- Modify after successful verification: `docs/planning/roadmap.md:64-90`
- Modify after successful verification: `docs/archive/plans/2026-08-24-task-detail-navigation.md`
- Delete after successful delivery: `docs/handoffs/task-detail-navigation-design.md`
- Modify after successful delivery: `docs/handoffs/INDEX.md`

**Interfaces:**

- Consumes: the three implementation commits and the approved specification acceptance criteria.
- Produces: fresh automated, asset, responsive-browser, architecture-boundary, terminology-boundary,
  and independent-review evidence.
- Guarantees: roadmap completion and handoff retirement are recorded only after the implementation
  and all required gates succeed.

- [ ] **Step 0: Claim the final verification issue**

Run:

```text
br update tas-task-detail-navigation-fjb.4 --status in_progress
```

Expected: `.4` is in progress after `.3` has closed.

- [ ] **Step 1: Run the complete focused regression suite**

Run:

```text
mix test test/taskman_web/components/core_components_test.exs
mix test test/taskman_web/live/project_live_test.exs
mix test test/taskman_web/live/project_live_autosave_test.exs
mix assets.build
```

Expected: every command exits 0 with zero test failures or asset compilation errors.

- [ ] **Step 2: Verify architectural and terminology boundaries**

Run:

```text
rg -n "Taskman\\.Repo|Ecto\\.Query" lib/taskman_web
rg -n -i "implementation plan|milestone|bead|task-detail-navigation-design" lib test assets
```

Expected: both searches exit 1 with no matches. If either search finds pre-existing unrelated
content, record the exact baseline and verify the increment added no new match; do not broaden this
work into unrelated cleanup.

- [ ] **Step 3: Run the repository gate**

Run:

```text
mix precommit
```

Expected: compilation with warnings as errors, unused dependency check, formatting, migrations, and
the complete test suite all exit 0. If PostgreSQL is unavailable, stop and report that exact
environment blocker rather than claiming verification.

- [ ] **Step 4: Repeat the approved responsive browser acceptance**

Repeat every desktop, tablet, and phone observation from Task 3 Step 6 against the final source
state after `mix precommit`. Capture concise evidence: tested viewport widths, preference behavior,
Escape ordering, outside dismissal, reflow, scroll ownership, focus, clipping, and content-sized
maximum height.

Expected: every acceptance criterion passes on the same source state used for final verification.

- [ ] **Step 5: Obtain independent implementation verification**

Give a fresh verifier the approved specification, this plan, the bounded implementation diff, and
the commands above. Require the verifier to:

1. inspect the changed files directly;
2. map them to every acceptance criterion;
3. rerun feasible focused checks;
4. inspect the colocated hook for listener cleanup, patch survival, and Escape ordering;
5. inspect the modal and CSS for content-sized maximum height and separate scroll ownership; and
6. report defects, omissions, and residual uncertainty without modifying the implementation.

Resolve any finding through a new failing focused test or reproducible browser observation, rerun
the affected gates, and obtain a clean re-review before continuing.

- [ ] **Step 6: Record roadmap completion only after all gates pass**

Replace slice 2's `**Current state:** Partially complete.` paragraphs in
`docs/planning/roadmap.md` with:

```markdown
**Current state:** Complete. The list-first Project workspace keeps the selected Project's direct
Task list behind a canonical URL-backed Task-detail modal. The modal supports complete autosaved
editing, explicit human-controlled lifecycle changes, recoverable invalid URLs, a collapsible
parent-child hierarchy shell, and separate truthful Activity and Sessions empty states. Hierarchy
preference lasts for the current workspace LiveView, wide layouts push the detail, and narrower
layouts use an internal overlay while Activity and Sessions reflow below the form. Focused tests,
the complete repository gate, independent implementation review, and responsive browser acceptance
have passed.
```

Keep later slices unchanged and do not imply that parent-child, Activity, or Agent Session
persistence exists.

- [ ] **Step 7: Persist evidence and retire the handoff**

Update this plan's status with the exact verification commands, test counts, browser widths, and
independent-review result.

Close the final child and parent only after that evidence and the roadmap update are ready:

```text
br close tas-task-detail-navigation-fjb.4 \
  --reason "All automated, browser, boundary, and independent verification gates passed."
br close tas-task-detail-navigation-fjb \
  --reason "All Task detail delivery units are closed and durable completion state is reconciled."
br show tas-task-detail-navigation-fjb tas-task-detail-navigation-fjb.4 --json
br sync --status
```

Delete `docs/handoffs/task-detail-navigation-design.md` and restore
`docs/handoffs/INDEX.md` to its no-current-handoffs state in the same change. Do not archive the
handoff.

- [ ] **Step 8: Commit only verified durable completion state**

Inspect and commit only:

```text
docs/planning/roadmap.md
docs/archive/plans/2026-08-24-task-detail-navigation.md
docs/handoffs/INDEX.md
docs/handoffs/task-detail-navigation-design.md
.beads/issues.jsonl
```

Use commit message:

```text
record Task detail delivery
```

Expected: the commit records verified completion and handoff retirement. It contains no unverified
product change, unrelated tracker change, generated build artifact, or visual-companion scratch
file.

---

## Completion evidence

Completed on 2026-08-24.

- Focused verification passed separately: 2 core-component tests, 20 ProjectLive tests, and 7
  autosave tests.
- `mix assets.build` and focused `mix format --check-formatted` verification passed.
- Both architectural and planning-terminology boundary searches exited 1 with no matches.
- `mix precommit` passed with 49 tests and zero failures.
- Responsive browser acceptance passed at effective CSS viewports 1280×900, 1024×800, and 390×842.
  It verified wide push layout, narrow overlay layout, preference persistence across Task patches,
  reload reset, outside dismissal, two-stage Escape behavior, focus retention, responsive reflow,
  content-sized maximum height, independent hierarchy/main scrolling, and no horizontal overflow.
- Browser console inspection found no warnings or errors.
- Independent implementation verification inspected the approved specification, plan, bounded
  commits, and current files; reran the focused tests, formatting, assets, boundary searches, and
  responsive browser matrix; and reported no Critical, Important, or Minor findings.
- The populated-hierarchy contextual default was inspected in code rather than exercised with
  persisted data because this increment intentionally adds no parent-child persistence.

---

## Plan review checklist

- Every approved specification requirement maps to Tasks 1 through 4.
- The implementation creates no persistence or operations for hierarchy, Activity, or Sessions.
- The component, hook, CSS, and LiveView responsibilities have one owner each.
- The explicit preference survives modal removal because the hook is mounted on the stable
  workspace.
- The preference resets on full reload because it is stored only on the hook instance.
- The narrow Escape listener runs on the workspace before the existing window-level modal handler.
- Wide and narrow geometry use the exact `80rem`, `15rem`, `18rem`, `3rem`, and drawer-width
  contracts from the specification.
- The modal uses maximum height without a forced height.
- Automated tests cover semantics and behavior without styling assertions.
- Browser acceptance covers the presentation behavior that server-rendered tests cannot prove.
- Final durable state is updated only after fresh verification and independent review.
