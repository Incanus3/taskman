defmodule TaskmanWeb.WorkspaceNavigation do
  use TaskmanWeb, :html

  alias Taskman.Lists.NavigationNode
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.ListEdit
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.TaskCountBadge

  @doc "Renders the selected Project root and its visible List tree."
  attr :task_counts, :map, required: true
  attr :navigation_nodes, :any, required: true
  attr :selected_project, Project, default: nil
  attr :selected_list, TaskList, default: nil
  attr :location_not_found?, :boolean, default: false
  attr :include_children?, :boolean, default: false
  attr :list_edit, ListEdit, required: true

  def tree(assigns) do
    ~H"""
    <nav
      :if={@selected_project}
      id="workspace-navigation"
      aria-label="Workspace navigation"
      class="flex min-h-0 flex-1 flex-col gap-1"
    >
      <div id="project-tasks-row" class="group/row relative shrink-0">
        <div class={[
          "flex items-center gap-1 rounded-xl px-2 py-1.5 transition hover:bg-white/7 hover:text-white focus-within:bg-white/7",
          is_nil(@selected_list) && !@location_not_found? && "bg-white/12 text-white",
          (!is_nil(@selected_list) || @location_not_found?) && "text-slate-300"
        ]}>
          <.link
            id="project-tasks-link"
            aria-describedby="project-tasks-count-description"
            patch={Paths.browse_path(@selected_project, nil, @include_children?)}
            aria-current={is_nil(@selected_list) && !@location_not_found? && "page"}
            class="flex min-w-0 flex-1 items-center gap-2 rounded-lg py-1 pl-1 pr-1 text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-indigo-400/50"
          >
            <.icon name="hero-clipboard-document-list" class="size-4 shrink-0 text-indigo-300" />
            <span class="truncate">Project tasks</span>
          </.link>
          <.row_controls>
            <:badge>
              <TaskCountBadge.badge
                id="project-tasks-count"
                visible={Map.get(@task_counts, nil, %{visible: 0}).visible}
              />
            </:badge>
            <button
              id={"add-root-list-#{@selected_project.id}"}
              type="button"
              phx-click="open_list_form"
              phx-value-kind="new"
              phx-value-parent-id=""
              phx-value-project-id={@selected_project.id}
              aria-label="Add root List"
              data-tooltip=""
              class="grid size-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >
              <.icon name="hero-plus" class="size-4" />
            </button>
          </.row_controls>
        </div>
        <.list_form :if={root_form_active?(@list_edit, @selected_project)} list_edit={@list_edit} />
      </div>
      <div
        id="workspace-tree"
        role="tree"
        aria-label="Lists in current Project"
        phx-update="stream"
        phx-hook=".NavigationDisclosureFocus"
        class="min-h-0 flex-1 space-y-1 overflow-y-auto"
      >
        <div
          :for={{dom_id, node} <- @navigation_nodes}
          id={dom_id}
          role="treeitem"
          aria-level={node.depth}
          aria-current={node.selected? && "page"}
          class="group/row relative"
        >
          <div
            class={[
              "relative flex items-center gap-1 rounded-xl px-2 py-1.5 transition",
              node.selected? && "bg-white/12 text-white",
              !node.selected? &&
                "text-slate-300 hover:bg-white/7 hover:text-white focus-within:bg-white/7"
            ]}
            style={"padding-inline-start: #{(node.depth - 1) * 1.25}rem"}
          >
            <button
              :if={node.expandable?}
              id={toggle_id(node)}
              type="button"
              phx-click="toggle_navigation_node"
              phx-value-kind="list"
              phx-value-id={node.task_list.id}
              phx-value-project-id={node.project.id}
              aria-expanded={to_string(node.expanded?)}
              aria-label={toggle_label(node)}
              data-tooltip=""
              class="group/disclosure grid size-8 shrink-0 place-items-center rounded-lg text-indigo-300 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >
              <.icon
                name={node.icon}
                class="size-4 group-hover/row:hidden group-focus-visible/disclosure:hidden"
              />
              <.icon
                name={if(node.expanded?, do: "hero-chevron-down", else: "hero-chevron-right")}
                class="hidden size-4 group-hover/row:block group-focus-visible/disclosure:block"
              />
            </button>
            <span
              :if={!node.expandable?}
              aria-hidden="true"
              class="grid size-8 shrink-0 place-items-center text-indigo-300"
            >
              <.icon name={node.icon} class="size-4" />
            </span>
            <.link
              id={selection_link_id(node)}
              patch={Paths.browse_path(node.project, node.task_list, @include_children?)}
              aria-current={node.selected? && "page"}
              aria-label={"Select #{node.task_list.name}"}
              aria-describedby={"list-task-count-#{node.task_list.id}-description"}
              class="flex min-w-0 flex-1 items-center rounded-lg py-1 pl-1 pr-1 text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-indigo-400/50"
            >
              <span class="min-w-0 flex-1 truncate">{node.task_list.name}</span>
            </.link>
            <.row_controls>
              <:badge>
                <TaskCountBadge.badge
                  id={"list-task-count-#{node.task_list.id}"}
                  visible={Map.get(@task_counts, node.task_list.id, %{visible: 0}).visible}
                />
              </:badge>
              <div
                id={"list-actions-#{node.task_list.id}"}
                class="flex items-center gap-1"
              >
                <button
                  id={"rename-list-#{node.task_list.id}"}
                  type="button"
                  phx-click="open_list_form"
                  phx-value-kind="rename"
                  phx-value-list-id={node.task_list.id}
                  phx-value-project-id={node.project.id}
                  aria-label={"Rename #{node.task_list.name}"}
                  data-tooltip="Rename List"
                  class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                >
                  <.icon name="hero-pencil-square" class="size-4" />
                </button>
                <button
                  id={"add-child-list-#{node.task_list.id}"}
                  type="button"
                  phx-click="open_list_form"
                  phx-value-kind="new"
                  phx-value-parent-id={node.task_list.id}
                  phx-value-project-id={node.project.id}
                  aria-label={"Add child List to #{node.task_list.name}"}
                  data-tooltip="Add child List"
                  class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                >
                  <.icon name="hero-plus" class="size-4" />
                </button>
              </div>
            </.row_controls>
          </div>
          <.list_form :if={ListEdit.active_for?(@list_edit, node)} list_edit={@list_edit} />
        </div>
      </div>
      <script :type={Phoenix.LiveView.ColocatedHook} name=".NavigationDisclosureFocus">
        export default {
          mounted() {
            this.onClick = event => {
              const button = event.target.closest('button[id^="toggle-list-"]')
              if (button && this.el.contains(button) && document.activeElement === button) {
                this.pendingFocusId = button.id
              }
            }
            this.el.addEventListener("click", this.onClick)
          },
          updated() {
            const id = this.pendingFocusId
            this.pendingFocusId = null
            const focusLost = document.activeElement === document.body ||
              document.activeElement === document.documentElement
            if (id && focusLost) this.el.querySelector(`#${id}`)?.focus({preventScroll: true})
          },
          destroyed() {
            this.el.removeEventListener("click", this.onClick)
          }
        }
      </script>
    </nav>
    """
  end

  slot :badge, required: true
  slot :inner_block, required: true

  defp row_controls(assigns) do
    ~H"""
    <div class="grid shrink-0 grid-cols-[auto_4rem] items-center gap-1 [@media(hover:hover)]:min-w-16 [@media(hover:hover)]:grid-cols-1 [@media(hover:hover)]:gap-0">
      <div class="col-start-1 row-start-1 justify-self-end [@media(hover:hover)]:transition-opacity [@media(hover:hover)]:group-hover/row:pointer-events-none [@media(hover:hover)]:group-hover/row:opacity-0 [@media(hover:hover)]:group-focus-within/row:pointer-events-none [@media(hover:hover)]:group-focus-within/row:opacity-0">
        {render_slot(@badge)}
      </div>
      <div class="col-start-2 row-start-1 flex w-16 items-center justify-self-end justify-end [@media(hover:hover)]:pointer-events-none [@media(hover:hover)]:col-start-1 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:transition-opacity [@media(hover:hover)]:group-hover/row:pointer-events-auto [@media(hover:hover)]:group-hover/row:opacity-100 [@media(hover:hover)]:group-focus-within/row:pointer-events-auto [@media(hover:hover)]:group-focus-within/row:opacity-100">
        {render_slot(@inner_block)}
      </div>
    </div>
    """
  end

  attr :list_edit, ListEdit, required: true

  defp list_form(assigns) do
    form_id = ListEdit.form_id(assigns.list_edit)
    assigns = assign(assigns, :form_id, form_id)

    ~H"""
    <div
      data-list-popover
      class="absolute left-9 right-1 top-full z-30 mt-1 rounded-xl border border-slate-700 bg-slate-900/80 p-3 shadow-lg shadow-black/20"
    >
      <.form
        for={@list_edit.form}
        id={@form_id}
        phx-change="validate_list"
        phx-submit="save_list"
        phx-click-away="cancel_list_form"
        phx-window-keydown="cancel_list_form"
        phx-key="escape"
        phx-mounted={JS.focus(to: "#list-name")}
        class="space-y-2"
      >
        <p class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
          {ListEdit.title(@list_edit)}
        </p>
        <.input
          id="list-name"
          field={@list_edit.form[:name]}
          type="text"
          label="List name"
          autocomplete="off"
          placeholder="Name this List"
          class="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/10"
          error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/10"
        />
        <div class="flex items-center justify-end gap-2 pt-1">
          <button
            id={"#{@form_id}-cancel"}
            type="button"
            phx-click="cancel_list_form"
            class="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-400 transition hover:bg-white/10 hover:text-white"
          >
            Cancel
          </button>
          <button
            id={"#{@form_id}-submit"}
            type="submit"
            phx-disable-with="Saving…"
            class="rounded-lg bg-indigo-500 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-indigo-400 disabled:cursor-wait disabled:opacity-60"
          >
            Save
          </button>
        </div>
      </.form>
    </div>
    """
  end

  defp root_form_active?(
         %ListEdit{project: %Project{id: project_id}, action: {:new, nil}},
         %Project{id: project_id}
       ),
       do: true

  defp root_form_active?(_list_edit, _project), do: false

  defp selection_link_id(%NavigationNode{task_list: %TaskList{id: id}}), do: "select-list-#{id}"
  defp toggle_id(%NavigationNode{task_list: %TaskList{id: id}}), do: "toggle-list-#{id}"

  defp toggle_label(%NavigationNode{task_list: %TaskList{name: name}, expanded?: expanded?}) do
    if expanded?, do: "Collapse #{name}", else: "Expand #{name}"
  end
end
