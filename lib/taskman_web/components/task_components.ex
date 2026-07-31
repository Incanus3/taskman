defmodule TaskmanWeb.TaskComponents do
  use TaskmanWeb, :html

  alias Taskman.Tasks.Task

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
  attr :task, Task, required: true
  attr :project_id, :integer, required: true

  def row(assigns) do
    ~H"""
    <article
      id={@id}
      class="relative grid gap-3 border-b border-slate-800 px-3 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_5.5rem_5.5rem] sm:items-center"
    >
      <.link
        id={"open-task-#{@task.id}"}
        patch={~p"/projects/#{@project_id}/tasks/#{@task.id}"}
        class="absolute inset-0 z-0 rounded-xl outline-none transition hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
      >
        <span class="sr-only">Open {@task.title}</span>
      </.link>
      <p
        id={"task-#{@task.id}"}
        class="pointer-events-none relative z-10 truncate text-sm font-medium text-slate-100"
      >
        {@task.title}
      </p>
      <span
        id={"task-status-#{@task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-amber-400/10 px-2.5 py-1 text-xs font-semibold text-amber-300 sm:justify-self-center"
      >
        {status_label(@task.status)}
      </span>
      <span
        id={"task-priority-#{@task.id}"}
        class="pointer-events-none relative z-10 w-fit rounded-full bg-slate-800 px-2.5 py-1 text-xs font-semibold text-slate-300 sm:justify-self-center"
      >
        {priority_label(@task.priority)}
      </span>
    </article>
    """
  end
end
