defmodule TaskmanWeb.Tasks.Detail do
  use TaskmanWeb, :html

  alias Taskman.Projects.Project
  alias Taskman.Tasks.{Hierarchy, HierarchyNode, Task}
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Move, ParentPicker}
  alias TaskmanWeb.ProjectLive.Tasks.Hierarchy, as: TaskHierarchy
  alias TaskmanWeb.Tasks.{Form, MovePopover}

  attr :task, Task, required: true
  attr :project, Project, default: nil
  attr :task_autosave, Autosave, required: true
  attr :parent_picker, ParentPicker, required: true
  attr :cancel, :string, required: true
  attr :task_hierarchy, TaskHierarchy, required: true
  attr :task_path, :any, required: true
  attr :browse_path, :any, default: nil
  attr :task_move, Move, required: true
  attr :recovery?, :boolean, default: false

  def detail(assigns) do
    assigns =
      assign(
        assigns,
        :location_path,
        TaskHierarchy.selected_location_path(assigns.task_hierarchy)
      )

    ~H"""
    <div
      id="task-detail-layout"
      data-recovery={to_string(@recovery?)}
      data-has-hierarchy={to_string(hierarchy_content?(@task_hierarchy))}
      data-hierarchy-expanded="false"
      class="task-detail-layout"
    >
      <div :if={!@recovery?} id="task-hierarchy-overlay" aria-hidden="true"></div>

      <aside
        :if={!@recovery?}
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
          <section
            class="min-w-0 p-6 sm:px-7 sm:pb-7"
            aria-labelledby={if(@recovery?, do: nil, else: "task-modal-title")}
          >
            <div :if={!@recovery?} class="relative mb-3">
              <div class="flex min-w-0 items-start justify-between gap-4 pr-10 xl:pr-0">
                <nav
                  id="task-location-breadcrumbs"
                  aria-label="Task location"
                  phx-hook="TaskmanWeb.Tasks.Detail.TaskLocationBreadcrumbs"
                  class="min-w-0 flex-1"
                >
                  <ol
                    id="task-location-breadcrumb-track"
                    class="flex min-w-0 items-center justify-start overflow-hidden"
                  >
                    <li
                      id="task-location-ellipsis"
                      hidden
                      aria-hidden="true"
                      class="flex shrink-0 items-center"
                    >
                      <span class="text-base font-semibold text-slate-500">…</span>
                      <.icon name="hero-chevron-right" class="mx-1.5 size-4 text-slate-600" />
                    </li>
                    <li
                      :if={@location_path == []}
                      data-containing-location
                      class="hidden min-w-0 shrink-0 items-center sm:flex"
                    >
                      <.link
                        id={"task-location-project-#{@project.id}"}
                        patch={@browse_path.(nil)}
                        data-containing-location
                        class="block max-w-64 truncate text-base font-semibold text-slate-400 transition hover:text-slate-200 xl:max-w-none"
                      >
                        {@project.name}
                      </.link>
                      <.icon
                        name="hero-chevron-right"
                        class="mx-1.5 size-4 shrink-0 text-slate-600"
                      />
                    </li>
                    <li
                      :for={{task_list, index} <- Enum.with_index(@location_path)}
                      data-optional-segment={index < length(@location_path) - 1 && "true"}
                      data-containing-location={index == length(@location_path) - 1 && "true"}
                      class={[
                        "items-center",
                        index < length(@location_path) - 1 && "hidden shrink-0 xl:flex",
                        index == length(@location_path) - 1 &&
                          "hidden min-w-0 shrink-0 sm:flex"
                      ]}
                    >
                      <.link
                        id={"task-location-list-#{task_list.id}"}
                        patch={@browse_path.(task_list)}
                        data-optional-segment={index < length(@location_path) - 1 && "true"}
                        data-containing-location={index == length(@location_path) - 1 && "true"}
                        class={[
                          "block whitespace-nowrap text-base font-semibold text-slate-400 transition hover:text-slate-200",
                          index == length(@location_path) - 1 &&
                            "max-w-64 truncate xl:max-w-none"
                        ]}
                      >
                        {task_list.name}
                      </.link>
                      <.icon
                        name="hero-chevron-right"
                        class="mx-1.5 size-4 shrink-0 text-slate-600"
                      />
                    </li>
                    <li id="task-location-current" aria-current="page" class="shrink-0">
                      <h2
                        id="task-modal-title"
                        class="whitespace-nowrap text-base font-semibold text-slate-100"
                      >
                        Task #{@task.id}
                      </h2>
                    </li>
                  </ol>
                </nav>
                <button
                  id={"move-task-detail-button-#{@task.id}"}
                  type="button"
                  phx-click={JS.push_focus() |> JS.push("open_move_task")}
                  phx-value-task-id={@task.id}
                  class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-800/90 px-3 py-1.5 text-sm font-semibold text-slate-100 shadow-sm transition hover:border-slate-500 hover:bg-slate-700 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
                >
                  <.icon name="hero-arrows-right-left" class="size-4" /> Move Task
                </button>
              </div>
              <div
                :if={Move.active_for?(@task_move, @task.id, :detail)}
                data-move-task-popover
                class="absolute right-0 top-full z-30 w-80 max-w-full"
              >
                <MovePopover.popover
                  task_id={@task.id}
                  task_move={@task_move}
                  window_escape?={false}
                  recovery?={@recovery?}
                />
              </div>
            </div>
            <Form.form
              form={@task_autosave.form}
              mode={:edit}
              change="autosave_task"
              submit="submit_task_edit"
              cancel={@cancel}
              parent_picker={@parent_picker}
              conflicts={@task_autosave.conflicts}
              field_states={@task_autosave.field_states}
              recovery?={@recovery?}
            />
          </section>

          <aside
            :if={!@recovery?}
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
    <script :type={Phoenix.LiveView.ColocatedHook} name=".TaskLocationBreadcrumbs">
      export default {
        mounted() {
          this.wide = window.matchMedia("(min-width: 80rem)")
          this.scheduleFit = () => {
            cancelAnimationFrame(this.fitFrame)
            this.fitFrame = requestAnimationFrame(() => this.fit())
          }
          this.resizeObserver = new ResizeObserver(this.scheduleFit)
          this.resizeObserver.observe(this.el)
          this.wide.addEventListener("change", this.scheduleFit)
          this.scheduleFit()
        },

        updated() {
          this.scheduleFit()
        },

        destroyed() {
          cancelAnimationFrame(this.fitFrame)
          this.resizeObserver?.disconnect()
          this.wide?.removeEventListener("change", this.scheduleFit)
        },

        fit() {
          const track = this.el.querySelector("#task-location-breadcrumb-track")
          const ellipsis = this.el.querySelector("#task-location-ellipsis")
          if (!track || !ellipsis) return

          const optionalSegments = [
            ...track.querySelectorAll("li[data-optional-segment='true']")
          ]
          const containingSegment = track.querySelector("li[data-containing-location]")
          const containingLink = containingSegment?.querySelector("a[data-containing-location]")

          optionalSegments.forEach(segment => segment.style.removeProperty("display"))
          containingLink?.style.removeProperty("max-width")
          ellipsis.hidden = true

          if (!this.wide.matches) return

          const overflows = () => this.visibleWidth(track) > track.clientWidth + 1

          for (const segment of optionalSegments) {
            if (!overflows()) break

            segment.style.display = "none"
            ellipsis.hidden = false
          }

          if (overflows() && containingSegment && containingLink) {
            const otherWidth = this.visibleWidth(track, containingSegment)

            const separatorWidth =
              containingSegment.getBoundingClientRect().width -
              containingLink.getBoundingClientRect().width

            containingLink.style.maxWidth = `${Math.max(
              track.clientWidth - otherWidth - separatorWidth,
              0
            )}px`
          }
        },

        visibleWidth(track, excludedSegment = null) {
          return [...track.children]
            .filter(segment =>
              segment !== excludedSegment &&
                !segment.hidden &&
                getComputedStyle(segment).display !== "none"
            )
            .reduce((width, segment) => width + segment.getBoundingClientRect().width, 0)
        }
      }
    </script>
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
