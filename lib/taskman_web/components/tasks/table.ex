defmodule TaskmanWeb.Tasks.Table do
  use TaskmanWeb, :html

  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.ProjectLive.Tasks.Move
  alias TaskmanWeb.Tasks.{MovePopover, Priority, Status}

  def status_options, do: Enum.map(Task.statuses(), &{status_label(&1), &1})
  def priority_options, do: Enum.map(Task.priorities(), &{priority_label(&1), &1})
  def status_label(status), do: Status.label(status)
  def priority_label(priority), do: Priority.label(priority)

  attr :form, Phoenix.HTML.Form, required: true
  attr :visible_statuses, :list, required: true
  attr :open?, :boolean, required: true

  attr :id, :string, default: "task-status-filter"
  attr :toggle_event, :string, default: "toggle_task_status_filter"
  attr :close_event, :string, default: "close_task_status_filter"
  attr :change_event, :string, default: "filter_task_statuses"
  attr :compact?, :boolean, default: false
  attr :top_layer?, :boolean, default: false
  attr :label, :string, default: "Statuses"

  def status_filter(assigns) do
    ~H"""
    <div
      id={@id}
      phx-click-away={@open? && @close_event}
      phx-window-keydown={@open? && !@top_layer? && @close_event}
      phx-key={@open? && "escape"}
      class={["relative", @compact? && "task-hierarchy-content ml-auto shrink-0"]}
    >
      <button
        id={@id <> "-button"}
        type="button"
        phx-click={@toggle_event}
        aria-label={@label}
        aria-controls={@id <> "-menu"}
        data-tooltip="Filter statuses"
        aria-haspopup="true"
        aria-expanded={to_string(@open?)}
        class={[
          "inline-flex size-10 cursor-pointer items-center justify-center gap-2 rounded-xl border text-xs font-semibold transition",
          !@compact? && "md:w-auto md:px-3",
          @open? &&
            "border-indigo-400/40 bg-indigo-400/15 text-indigo-100",
          !@open? &&
            "border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:bg-slate-800 hover:text-white"
        ]}
      >
        <.icon name="hero-funnel" class={["size-5", !@compact? && "md:size-4"]} />
        <span :if={!@compact?} class="hidden md:inline">Statuses</span>
        <.icon
          :if={!@compact?}
          name="hero-chevron-down"
          class={[
            "hidden size-3.5 transition-transform md:inline-block",
            @open? && "rotate-180"
          ]}
        />
      </button>

      <div
        :if={@open?}
        id={@id <> "-menu"}
        popover={if(@top_layer?, do: "manual")}
        data-hierarchy-popup={if(@top_layer?, do: "", else: nil)}
        data-popup-trigger={@top_layer? && @id <> "-button"}
        data-popup-close={@top_layer? && @close_event}
        class={[
          "w-52 rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-xl shadow-black/30",
          if(@top_layer?,
            do: "hierarchy-popup-menu",
            else: "absolute right-0 top-full z-30 mt-2"
          )
        ]}
      >
        <.form
          for={@form}
          id={@id <> "-form"}
          phx-change={@change_event}
          class="space-y-1"
        >
          <input type="hidden" name={@form[:statuses].name <> "[]"} value="" />
          <label
            :for={{label, status} <- status_options()}
            for={"#{@id}-option-#{status}"}
            class="flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-sm text-slate-200 transition hover:bg-white/[0.06]"
          >
            <input
              id={"#{@id}-option-#{status}"}
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
          "group flex h-full w-full cursor-pointer items-center gap-1 rounded-md px-1 py-3.5 text-left outline-none transition hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-indigo-400/50 max-xl:py-2",
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
  attr :actions_enabled?, :boolean, default: true

  attr :depth, :integer, default: 0
  attr :expandable?, :boolean, default: false
  attr :expanded?, :boolean, default: false
  attr :last_sibling?, :boolean, default: true
  attr :ancestor_continuations, :list, default: []
  attr :children_visible?, :boolean, default: false

  def row(assigns) do
    continuation_lanes = Enum.map(assigns.ancestor_continuations, &min(&1, 6))

    continuation_lanes =
      if assigns.depth >= 6 and assigns.children_visible?,
        do: [6 | continuation_lanes],
        else: continuation_lanes

    assigns =
      assigns
      |> assign(:continuation_lanes, Enum.uniq(continuation_lanes))
      |> assign(:branch_shares_rail?, min(assigns.depth, 6) in continuation_lanes)
      |> assign(
        :show_child_trunk?,
        assigns.children_visible? and min(assigns.depth + 1, 6) not in continuation_lanes
      )

    ~H"""
    <% task = @task_with_location.task %>
    <article
      id={@id}
      data-task-depth={@depth}
      class="task-listing-row relative grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 px-3 py-3 xl:col-span-full xl:grid-cols-subgrid xl:gap-x-0.5 xl:gap-y-0"
    >
      <.link
        id={"open-task-#{task.id}"}
        patch={@task_path}
        class="absolute inset-0 z-0 rounded-xl outline-none transition hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
      >
        <span class="sr-only">Open {task.title}</span>
      </.link>
      <div
        id={"task-expansion-cell-#{task.id}"}
        class={[
          "relative z-10 self-center sm:row-span-2 xl:row-span-1",
          if(@include_children?, do: "row-span-3", else: "row-span-2")
        ]}
      >
        <button
          :if={@expandable?}
          id={"toggle-task-subtasks-#{task.id}"}
          type="button"
          phx-click="toggle_task_subtasks"
          phx-value-task-id={task.id}
          aria-label={"#{if @expanded?, do: "Collapse", else: "Expand"} subtasks for #{task.title}"}
          aria-expanded={to_string(@expanded?)}
          class="grid size-8 cursor-pointer place-items-center rounded-lg text-slate-400 transition hover:bg-indigo-400/10 hover:text-indigo-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <.icon
            name={if(@expanded?, do: "hero-chevron-down", else: "hero-chevron-right")}
            class="size-4"
          />
        </button>
      </div>
      <div
        id={"task-identity-#{task.id}"}
        class={[
          "task-listing-identity pointer-events-none relative z-10 self-stretch col-span-2 col-start-2 row-start-1 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] grid-rows-subgrid gap-x-2 sm:col-span-1 sm:col-start-2 sm:row-span-2 sm:row-start-1 xl:contents",
          if(@include_children?, do: "row-span-3", else: "row-span-2")
        ]}
      >
        <span
          id={"task-number-#{task.id}"}
          aria-label={"Task number: #{task.id}"}
          class="task-listing-number row-start-1 self-center xl:row-auto text-xs font-medium tabular-nums text-slate-500 xl:justify-self-start xl:pl-2 xl:text-sm"
        >
          <span class="xl:hidden">#</span>{task.id}
        </span>
        <div
          class="task-listing-title relative col-start-2 row-span-full row-start-1 grid min-w-0 grid-rows-subgrid self-stretch xl:col-auto xl:row-auto xl:items-center"
          style={"--task-depth: #{min(@depth, 6)}"}
        >
          <span
            :if={@depth > 0 || @children_visible?}
            id={"task-tree-guides-#{task.id}"}
            aria-hidden="true"
            class="task-tree-guides"
          >
            <span
              :for={lane <- @continuation_lanes}
              class="task-tree-rail"
              style={"--tree-lane: #{lane}"}
            />
            <span
              :if={@depth > 0}
              class={[
                "task-tree-branch",
                @last_sibling? && "task-tree-branch-last",
                @branch_shares_rail? && "task-tree-branch-shared"
              ]}
              style={"--tree-lane: #{min(@depth, 6)}"}
            />
            <span
              :if={@show_child_trunk?}
              class="task-tree-trunk"
              style={"--tree-lane: #{min(@depth + 1, 6)}"}
            />
          </span>
          <p
            id={"task-#{task.id}"}
            class="pointer-events-none row-start-1 self-center truncate text-sm font-medium text-slate-100"
          >
            <span :if={@depth > 0} class="sr-only">Subtask depth: {@depth}. </span>{task.title}
          </p>
        </div>
      </div>
      <p
        :if={@include_children?}
        id={"task-location-cell-#{task.id}"}
        aria-label={"Location: #{location_label(@task_with_location.location_path)}"}
        title={location_label(@task_with_location.location_path)}
        class="pointer-events-none relative z-10 col-span-2 col-start-2 row-start-2 max-w-full min-w-0 justify-self-start truncate text-left text-xs font-medium text-slate-400 sm:col-span-1 sm:col-start-2 sm:row-start-2 xl:col-auto xl:row-auto xl:max-w-none xl:justify-self-stretch xl:text-center xl:text-sm"
      >
        {location_label(@task_with_location.location_path)}
      </p>
      <div
        id={"task-badges-#{task.id}"}
        class={[
          "pointer-events-none relative z-10 col-start-2 flex min-w-0 flex-wrap items-center gap-2 sm:col-start-3 sm:row-start-1 sm:flex-nowrap sm:justify-end xl:contents",
          if(@include_children?, do: "row-start-3", else: "row-start-2")
        ]}
      >
        <span
          id={"task-status-#{task.id}"}
          class={[
            "pointer-events-none relative z-10 w-fit rounded-full px-2.5 py-1 text-xs font-semibold xl:justify-self-center",
            Status.badge_classes(task.status)
          ]}
        >
          {status_label(task.status)}
        </span>
        <span
          id={"task-priority-#{task.id}"}
          class={[
            "pointer-events-none relative z-10 w-fit rounded-full px-2.5 py-1 text-xs font-semibold xl:justify-self-center",
            Priority.badge_classes(task.priority)
          ]}
        >
          {priority_label(task.priority)}
        </span>
      </div>
      <div
        :if={@actions_enabled?}
        id={"task-actions-#{task.id}"}
        class={[
          "pointer-events-auto relative col-start-3 flex justify-end gap-2 sm:row-start-2 xl:col-auto xl:row-auto xl:justify-self-center",
          if(@include_children?, do: "row-start-3", else: "row-start-2"),
          Move.active_for?(@task_move, task.id, :row) && "z-40",
          !Move.active_for?(@task_move, task.id, :row) && "z-10"
        ]}
      >
        <.link
          :if={@add_subtask_path}
          id={"add-subtask-#{task.id}"}
          patch={@add_subtask_path}
          aria-label={"Add subtask to #{task.title}"}
          data-tooltip="Add subtask"
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
          data-tooltip="Move Task"
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

  defp aria_sort(nil), do: "none"
  defp aria_sort(:asc), do: "ascending"
  defp aria_sort(:desc), do: "descending"
  defp location_label([]), do: "Project"
  defp location_label(location_path), do: Enum.map_join(location_path, " / ", & &1.name)
end
