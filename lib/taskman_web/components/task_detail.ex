defmodule TaskmanWeb.TaskDetail do
  use TaskmanWeb, :html

  alias Taskman.Tasks.Task
  alias TaskmanWeb.{TaskAutosave, TaskForm, TaskMove}
  alias TaskmanWeb.TaskMove.Popover

  attr :task, Task, required: true
  attr :task_autosave, TaskAutosave, required: true
  attr :cancel, :string, required: true
  attr :has_hierarchy?, :boolean, default: false
  attr :task_move, TaskMove, required: true

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
        <div class="task-hierarchy-header flex items-center gap-2 p-3">
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
          <section class="min-w-0 p-6 sm:px-7 sm:pb-7" aria-labelledby="task-modal-title">
            <div class="mb-5 flex items-start justify-between gap-4 pr-10">
              <h2 id="task-modal-title" class="text-xl font-semibold tracking-tight text-slate-100">
                Task
              </h2>
              <button
                id={"move-task-detail-button-#{@task.id}"}
                type="button"
                phx-click={JS.push_focus() |> JS.push("open_move_task")}
                phx-value-task-id={@task.id}
                class="rounded-lg px-2 py-1 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
              >
                Move Task
              </button>
            </div>
            <Popover.popover
              :if={TaskMove.active_for?(@task_move, @task.id, :detail)}
              task_id={@task.id}
              task_move={@task_move}
              window_escape?={false}
            />
            <TaskForm.form
              form={@task_autosave.form}
              mode={:edit}
              change="autosave_task"
              submit="submit_task_edit"
              cancel={@cancel}
            />
            <p
              id="task-save-status"
              aria-live="polite"
              data-state={@task_autosave.save_state}
              class="mt-4 text-right text-sm text-slate-400"
            >
              {TaskAutosave.message(@task_autosave)}
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
