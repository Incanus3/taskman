defmodule TaskmanWeb.Tasks.Table do
  use TaskmanWeb, :html

  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.ProjectLive.Tasks.Move
  alias TaskmanWeb.Tasks.MovePopover

  @status_labels %{
    icebox: "Icebox",
    pending: "Pending",
    in_progress: "In Progress",
    in_review: "In Review",
    done: "Done",
    will_not_do: "Will Not Do"
  }

  @priority_labels %{
    none: "None",
    low: "Low",
    medium: "Medium",
    high: "High",
    urgent: "Urgent"
  }

  @status_classes %{
    icebox: "bg-sky-400/10 text-sky-300",
    pending: "bg-amber-400/10 text-amber-300",
    in_progress: "bg-red-400/10 text-red-300",
    in_review: "bg-orange-400/10 text-orange-300",
    done: "bg-emerald-400/10 text-emerald-300",
    will_not_do: "bg-slate-700/60 text-slate-400"
  }

  @priority_classes %{
    none: "bg-slate-800 text-slate-300",
    low: "bg-blue-400/10 text-blue-300",
    medium: "bg-emerald-400/10 text-emerald-300",
    high: "bg-amber-400/10 text-amber-300",
    urgent: "bg-red-400/10 text-red-300"
  }

  def status_options, do: Enum.map(Task.statuses(), &{status_label(&1), &1})
  def priority_options, do: Enum.map(Task.priorities(), &{priority_label(&1), &1})
  def status_label(status), do: Map.fetch!(@status_labels, status)
  def priority_label(priority), do: Map.fetch!(@priority_labels, priority)

  attr :form, Phoenix.HTML.Form, required: true
  attr :visible_statuses, :list, required: true
  attr :open?, :boolean, required: true

  def status_filter(assigns) do
    ~H"""
    <div
      id="task-status-filter"
      phx-hook=".TaskStatusFilterStorage"
      data-task-statuses={Enum.map_join(Task.statuses(), ",", &Atom.to_string/1)}
      phx-click-away={@open? && "close_task_status_filter"}
      phx-window-keydown={@open? && "close_task_status_filter"}
      phx-key={@open? && "escape"}
      class="relative"
    >
      <button
        id="task-status-filter-button"
        type="button"
        phx-click="toggle_task_status_filter"
        aria-haspopup="true"
        aria-expanded={to_string(@open?)}
        class={[
          "inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl border px-3 text-xs font-semibold transition",
          @open? &&
            "border-indigo-400/40 bg-indigo-400/15 text-indigo-100",
          !@open? &&
            "border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:bg-slate-800 hover:text-white"
        ]}
      >
        <.icon name="hero-funnel" class="size-4" />
        <span>Statuses</span>
        <.icon
          name="hero-chevron-down"
          class={[
            "size-3.5 transition-transform",
            @open? && "rotate-180"
          ]}
        />
      </button>

      <div
        :if={@open?}
        id="task-status-filter-menu"
        class="absolute right-0 top-full z-30 mt-2 w-52 rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-xl shadow-black/30"
      >
        <.form
          for={@form}
          id="task-status-filter-form"
          phx-change="filter_task_statuses"
          class="space-y-1"
        >
          <input type="hidden" name={@form[:statuses].name <> "[]"} value="" />
          <label
            :for={{label, status} <- status_options()}
            for={"task-status-filter-option-#{status}"}
            class="flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-sm text-slate-200 transition hover:bg-white/[0.06]"
          >
            <input
              id={"task-status-filter-option-#{status}"}
              type="checkbox"
              name={@form[:statuses].name <> "[]"}
              value={status}
              checked={status in @visible_statuses}
              data-task-status={status}
              class="size-4 rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-2 focus:ring-indigo-400/40 focus:ring-offset-0"
            />
            <span>{label}</span>
          </label>
        </.form>
      </div>

      <script :type={Phoenix.LiveView.ColocatedHook} name=".TaskStatusFilterStorage">
        export default {
          mounted() {
            this.storageKey = "taskman.task-table.visible-statuses"
            this.persistSelection = event => {
              if (!event.target.matches("[data-task-status]")) return

              const statuses = Array.from(
                this.el.querySelectorAll("[data-task-status]:checked"),
                input => input.dataset.taskStatus
              )

              try {
                window.localStorage.setItem(this.storageKey, JSON.stringify(statuses))
              } catch (_error) {
                // Filtering remains usable when browser storage is unavailable.
              }
            }

            this.el.addEventListener("change", this.persistSelection)
            this.restoreSelection()
          },

          destroyed() {
            this.el.removeEventListener("change", this.persistSelection)
          },

          restoreSelection() {
            let stored

            try {
              stored = window.localStorage.getItem(this.storageKey)
            } catch (_error) {
              return
            }

            if (stored === null) return

            try {
              const parsed = JSON.parse(stored)
              if (!Array.isArray(parsed)) return

              const allowed = new Set(this.el.dataset.taskStatuses.split(","))

              const statuses = [...new Set(
                parsed.filter(status => typeof status === "string" && allowed.has(status))
              )]

              this.pushEvent("restore_task_statuses", {statuses})
            } catch (_error) {
              // Ignore malformed preferences and retain the server default.
            }
          }
        }
      </script>
    </div>
    """
  end

  attr :id, :string, required: true
  attr :label, :string, required: true
  attr :field, :atom, required: true
  attr :task_sort, :any, default: nil
  attr :class, :string, default: nil

  def sort_header(assigns) do
    direction =
      case assigns.task_sort do
        {field, direction} when field == assigns.field -> direction
        _other -> nil
      end

    assigns = assign(assigns, :direction, direction)

    ~H"""
    <span
      id={@id}
      role="columnheader"
      aria-sort={aria_sort(@direction)}
      class="h-full w-full self-stretch"
    >
      <button
        id={"sort-task-#{@field}"}
        type="button"
        phx-click="sort_tasks"
        phx-value-field={@field}
        class={[
          "group flex h-full w-full cursor-pointer items-center gap-1 rounded-md px-1 py-3.5 text-left outline-none transition hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-indigo-400/50",
          @class
        ]}
      >
        <span>{@label}</span>
        <.icon
          :if={@direction}
          name={if(@direction == :asc, do: "hero-chevron-up", else: "hero-chevron-down")}
          class="size-3.5 text-indigo-300"
        />
      </button>
    </span>
    """
  end

  attr :id, :string, required: true
  attr :task_with_location, TaskWithLocation, required: true
  attr :task_path, :string, required: true
  attr :include_children?, :boolean, default: false
  attr :task_move, Move, required: true
  attr :add_subtask_path, :string, default: nil

  def row(assigns) do
    ~H"""
    <% task = @task_with_location.task %>
    <article
      id={@id}
      class="relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b border-slate-800 px-3 py-3 last:border-b-0 sm:col-span-full sm:grid-cols-subgrid sm:gap-x-0.5 sm:gap-y-0"
    >
      <.link
        id={"open-task-#{task.id}"}
        patch={@task_path}
        class="absolute inset-0 z-0 rounded-xl outline-none transition hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
      >
        <span class="sr-only">Open {task.title}</span>
      </.link>
      <div
        id={"task-identity-#{task.id}"}
        class={[
          "pointer-events-none relative z-10 flex min-w-0 items-baseline gap-2",
          !@include_children? && "col-span-2",
          "sm:contents"
        ]}
      >
        <span
          id={"task-number-#{task.id}"}
          aria-label={"Task number: #{task.id}"}
          class="shrink-0 text-xs font-medium tabular-nums text-slate-500 sm:justify-self-start sm:pl-2 sm:text-sm"
        >
          <span class="sm:hidden">#</span>{task.id}
        </span>
        <div class="min-w-0">
          <p
            id={"task-#{task.id}"}
            class="pointer-events-none truncate text-sm font-medium text-slate-100"
          >
            {task.title}
          </p>
        </div>
      </div>
      <p
        :if={@include_children?}
        id={"task-location-cell-#{task.id}"}
        aria-label={"Location: #{location_label(@task_with_location.location_path)}"}
        title={location_label(@task_with_location.location_path)}
        class="pointer-events-none relative z-10 max-w-[45vw] min-w-0 justify-self-end truncate text-right text-xs font-medium text-slate-400 sm:max-w-none sm:justify-self-stretch sm:text-center sm:text-sm"
      >
        {location_label(@task_with_location.location_path)}
      </p>
      <div
        id={"task-badges-#{task.id}"}
        class="pointer-events-none relative z-10 flex min-w-0 items-center gap-2 sm:contents"
      >
        <span
          id={"task-status-#{task.id}"}
          class={[
            "pointer-events-none relative z-10 w-fit rounded-full px-2.5 py-1 text-xs font-semibold sm:justify-self-center",
            status_classes(task.status)
          ]}
        >
          {status_label(task.status)}
        </span>
        <span
          id={"task-priority-#{task.id}"}
          class={[
            "pointer-events-none relative z-10 w-fit rounded-full px-2.5 py-1 text-xs font-semibold sm:justify-self-center",
            priority_classes(task.priority)
          ]}
        >
          {priority_label(task.priority)}
        </span>
      </div>
      <div
        id={"task-actions-#{task.id}"}
        class={[
          "pointer-events-auto relative flex justify-end gap-2 sm:justify-self-center",
          Move.active_for?(@task_move, task.id, :row) && "z-40",
          !Move.active_for?(@task_move, task.id, :row) && "z-10"
        ]}
      >
        <.link
          :if={@add_subtask_path}
          id={"add-subtask-#{task.id}"}
          patch={@add_subtask_path}
          aria-label={"Add subtask to #{task.title}"}
          title="Add subtask"
          class="pointer-events-auto grid size-8 place-items-center rounded-lg border border-slate-700 bg-slate-800 text-slate-300 shadow-sm transition hover:border-indigo-400/50 hover:bg-indigo-400/10 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
        >
          <.icon name="hero-plus" class="size-4" />
        </.link>
        <button
          id={"move-task-row-button-#{task.id}"}
          type="button"
          phx-click={JS.push_focus() |> JS.push("open_move_task")}
          phx-value-task-id={task.id}
          aria-label={"Move #{task.title}"}
          title="Move Task"
          class="pointer-events-auto grid size-8 cursor-pointer place-items-center rounded-lg border border-slate-700 bg-slate-800 text-slate-300 shadow-sm transition hover:border-indigo-400/50 hover:bg-indigo-400/10 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
        >
          <.icon name="hero-arrows-right-left" class="size-4" />
        </button>
        <div
          :if={Move.active_for?(@task_move, task.id, :row)}
          data-move-task-popover
          phx-remove={JS.pop_focus()}
          class="absolute right-0 top-full z-30 w-80 max-w-[calc(100vw-3rem)]"
        >
          <MovePopover.popover
            task_id={task.id}
            task_move={@task_move}
            restore_focus?={false}
          />
        </div>
      </div>
    </article>
    """
  end

  defp status_classes(status), do: Map.fetch!(@status_classes, status)
  defp priority_classes(priority), do: Map.fetch!(@priority_classes, priority)
  defp aria_sort(nil), do: "none"
  defp aria_sort(:asc), do: "ascending"
  defp aria_sort(:desc), do: "descending"
  defp location_label([]), do: "Project"
  defp location_label(location_path), do: Enum.map_join(location_path, " / ", & &1.name)
end
