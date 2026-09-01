defmodule TaskmanWeb.TaskDetail do
  use TaskmanWeb, :html

  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.{TaskAutosave, TaskForm, TaskHierarchy, TaskMove, TaskParentPicker}
  alias TaskmanWeb.TaskMove.Popover

  attr :task, Task, required: true
  attr :task_autosave, TaskAutosave, required: true
  attr :parent_picker, TaskParentPicker, required: true
  attr :cancel, :string, required: true
  attr :task_hierarchy, TaskHierarchy, required: true
  attr :task_path, :any, required: true
  attr :task_move, TaskMove, required: true

  def detail(assigns) do
    ~H"""
    <div
      id="task-detail-layout"
      data-has-hierarchy={to_string(hierarchy_content?(@task_hierarchy))}
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

        <div class="task-hierarchy-content ms-3 px-3 pb-5">
          <ul role="tree" aria-label="Task hierarchy" class="border-l border-indigo-400/50 pl-3">
            <.hierarchy_node
              node={@task_hierarchy.hierarchy.root}
              task_hierarchy={@task_hierarchy}
              task_path={@task_path}
              level={1}
            />
          </ul>
          <p
            :if={!hierarchy_content?(@task_hierarchy)}
            id="task-hierarchy-empty"
            class="mt-4 text-xs leading-5 text-slate-400"
          >
            No parent or child Tasks
          </p>
        </div>
      </aside>

      <div id="task-detail-content" class="task-detail-content">
        <div class="task-detail-columns">
          <section class="min-w-0 p-6 sm:px-7 sm:pb-7" aria-labelledby="task-modal-title">
            <div class="relative mb-5">
              <div class="flex items-start justify-between gap-4 pr-10 xl:pr-0">
                <h2
                  id="task-modal-title"
                  class="text-xl font-semibold tracking-tight text-slate-100"
                >
                  Task #{@task.id}
                </h2>
                <button
                  id={"move-task-detail-button-#{@task.id}"}
                  type="button"
                  phx-click={JS.push_focus() |> JS.push("open_move_task")}
                  phx-value-task-id={@task.id}
                  class="cursor-pointer rounded-lg px-2 py-1 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
                >
                  Move Task
                </button>
              </div>
              <div
                :if={TaskMove.active_for?(@task_move, @task.id, :detail)}
                data-move-task-popover
                class="absolute left-0 right-0 top-full z-30"
              >
                <Popover.popover
                  task_id={@task.id}
                  task_move={@task_move}
                  window_escape?={false}
                />
              </div>
            </div>
            <TaskForm.form
              form={@task_autosave.form}
              mode={:edit}
              change="autosave_task"
              submit="submit_task_edit"
              cancel={@cancel}
              parent_picker={@parent_picker}
              conflicts={@task_autosave.conflicts}
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

  attr :node, HierarchyNode, required: true
  attr :task_hierarchy, TaskHierarchy, required: true
  attr :task_path, :any, required: true
  attr :level, :integer, required: true

  def hierarchy_node(assigns) do
    collapsible? =
      has_children?(assigns.node) &&
        TaskHierarchy.collapsible?(assigns.task_hierarchy, assigns.node.task.id)

    assigns = assign(assigns, :collapsible?, collapsible?)

    ~H"""
    <li
      id={"task-hierarchy-node-#{@node.task.id}"}
      role="treeitem"
      aria-current={if(@node.task.id == @task_hierarchy.hierarchy.selected_task_id, do: "true")}
      aria-expanded={
        if(has_children?(@node),
          do: to_string(TaskHierarchy.expanded?(@task_hierarchy, @node.task.id))
        )
      }
      aria-level={@level}
      class={[
        "task-hierarchy-node",
        @node.task.id == @task_hierarchy.hierarchy.selected_task_id && "task-hierarchy-node-current"
      ]}
    >
      <div class="task-hierarchy-node-row">
        <button
          :if={@collapsible?}
          id={"task-hierarchy-disclosure-#{@node.task.id}"}
          type="button"
          phx-click="toggle_task_hierarchy_node"
          phx-value-task-id={@node.task.id}
          aria-expanded={to_string(TaskHierarchy.expanded?(@task_hierarchy, @node.task.id))}
          aria-label={"Toggle #{String.trim(@node.task.title || "")}"}
          class="task-hierarchy-disclosure"
        >
          <.icon
            name={
              if(TaskHierarchy.expanded?(@task_hierarchy, @node.task.id),
                do: "hero-chevron-down",
                else: "hero-chevron-right"
              )
            }
            class="size-3.5"
          />
        </button>
        <.link
          id={"task-hierarchy-link-#{@node.task.id}"}
          patch={@task_path.(@node.task)}
          aria-current={if(@node.task.id == @task_hierarchy.hierarchy.selected_task_id, do: "true")}
          class="task-hierarchy-link"
        >
          {@node.task.title}
        </.link>
      </div>

      <ul
        :if={has_children?(@node) && TaskHierarchy.expanded?(@task_hierarchy, @node.task.id)}
        id={"task-hierarchy-group-#{@node.task.id}"}
        role="group"
        class="task-hierarchy-group"
      >
        <.hierarchy_node
          :for={child <- @node.children}
          node={child}
          task_hierarchy={@task_hierarchy}
          task_path={@task_path}
          level={@level + 1}
        />
      </ul>
    </li>
    """
  end

  defp hierarchy_content?(%TaskHierarchy{
         hierarchy: %Hierarchy{
           root: %HierarchyNode{task: root_task, children: children},
           selected_task_id: selected_task_id
         }
       }) do
    root_task.id != selected_task_id or children != []
  end

  defp hierarchy_content?(%TaskHierarchy{}), do: false

  defp has_children?(%HierarchyNode{children: children}), do: children != []
end
