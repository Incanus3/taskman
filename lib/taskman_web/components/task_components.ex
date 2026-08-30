defmodule TaskmanWeb.TaskComponents do
  use TaskmanWeb, :html

  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.TaskMove
  alias TaskmanWeb.TaskMove.Popover

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

  def status_options, do: Enum.map(Task.statuses(), &{status_label(&1), &1})
  def priority_options, do: Enum.map(Task.priorities(), &{priority_label(&1), &1})
  def status_label(status), do: Map.fetch!(@status_labels, status)
  def priority_label(priority), do: Map.fetch!(@priority_labels, priority)

  attr :id, :string, required: true
  attr :task_with_location, TaskWithLocation, required: true
  attr :task_path, :string, required: true
  attr :include_children?, :boolean, default: false
  attr :task_move, TaskMove, required: true
  attr :add_subtask_path, :string, default: nil

  def row(assigns) do
    ~H"""
    <% task = @task_with_location.task %>
    <article
      id={@id}
      class="relative grid gap-3 border-b border-slate-800 px-3 py-3 last:border-b-0 sm:col-span-full sm:grid-cols-subgrid sm:items-center sm:gap-x-0.5"
    >
      <.link
        id={"open-task-#{task.id}"}
        patch={@task_path}
        class="absolute inset-0 z-0 rounded-xl outline-none transition hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
      >
        <span class="sr-only">Open {task.title}</span>
      </.link>
      <div class="pointer-events-none relative z-10 min-w-0">
        <p
          id={"task-#{task.id}"}
          class="pointer-events-none truncate text-sm font-medium text-slate-100"
        >
          {task.title}
        </p>
      </div>
      <p
        :if={@include_children?}
        id={"task-location-cell-#{task.id}"}
        aria-label={"Location: #{location_label(@task_with_location.location_path)}"}
        title={location_label(@task_with_location.location_path)}
        class="pointer-events-none relative z-10 min-w-0 truncate text-center text-xs font-medium text-slate-400 sm:text-sm"
      >
        {location_label(@task_with_location.location_path)}
      </p>
      <span
        id={"task-status-#{task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-amber-400/10 px-2.5 py-1 text-xs font-semibold text-amber-300 sm:justify-self-center"
      >
        {status_label(task.status)}
      </span>
      <span
        id={"task-priority-#{task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-slate-800 px-2.5 py-1 text-xs font-semibold text-slate-300 sm:justify-self-center"
      >
        {priority_label(task.priority)}
      </span>
      <div
        id={"task-actions-#{task.id}"}
        class={[
          "pointer-events-auto relative flex justify-end gap-2 sm:justify-self-center",
          TaskMove.active_for?(@task_move, task.id, :row) && "z-40",
          !TaskMove.active_for?(@task_move, task.id, :row) && "z-10"
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
          class="pointer-events-auto grid size-8 place-items-center rounded-lg border border-slate-700 bg-slate-800 text-slate-300 shadow-sm transition hover:border-indigo-400/50 hover:bg-indigo-400/10 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
        >
          <.icon name="hero-arrows-right-left" class="size-4" />
        </button>
        <div
          :if={TaskMove.active_for?(@task_move, task.id, :row)}
          data-move-task-popover
          phx-remove={JS.pop_focus()}
          class="absolute right-0 top-full z-30 w-80 max-w-[calc(100vw-3rem)]"
        >
          <Popover.popover
            task_id={task.id}
            task_move={@task_move}
            restore_focus?={false}
          />
        </div>
      </div>
    </article>
    """
  end

  defp location_label([]), do: "Project"
  defp location_label(location_path), do: Enum.map_join(location_path, " / ", & &1.name)
end
