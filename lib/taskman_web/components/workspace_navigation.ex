defmodule TaskmanWeb.WorkspaceNavigation do
  use TaskmanWeb, :html

  alias Taskman.Lists.NavigationNode
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias TaskmanWeb.ListEdit

  @doc """
  Renders the visible Project/List portion of the workspace sidebar.

  The LiveView owns the navigation stream and all transient state. This component only projects
  each supplied flattened node into semantic tree markup and the currently active List form.
  """
  attr :navigation_nodes, :any, required: true
  attr :include_children?, :boolean, default: false
  attr :list_edit, ListEdit, required: true

  def tree(assigns) do
    ~H"""
    <nav
      id="workspace-tree"
      role="tree"
      aria-label="Workspace navigation"
      phx-update="stream"
      class="space-y-1"
    >
      <div
        id="projects-empty"
        class="hidden rounded-xl border border-dashed border-slate-700 px-3 py-4 text-sm text-slate-400 only:block"
      >
        Create your first Project below.
      </div>
      <div
        :for={{dom_id, node} <- @navigation_nodes}
        id={dom_id}
        role="treeitem"
        aria-level={node.depth}
        aria-current={node.selected? && "page"}
        class="group relative"
      >
        <div
          class={[
            "flex items-center gap-1 rounded-xl px-2 py-1.5 transition",
            node.selected? && "bg-white/12 text-white",
            !node.selected? && "text-slate-300 hover:bg-white/7 hover:text-white"
          ]}
          style={"padding-inline-start: #{(node.depth - 1) * 1.25}rem"}
        >
          <button
            :if={node.expandable?}
            id={toggle_id(node)}
            type="button"
            phx-click="toggle_navigation_node"
            phx-value-kind={node.kind}
            phx-value-id={node_id(node)}
            phx-value-project-id={node.project.id}
            aria-expanded={to_string(node.expanded?)}
            aria-label={toggle_label(node)}
            class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-white/10 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/40"
          >
            <.icon
              name={if(node.expanded?, do: "hero-chevron-down", else: "hero-chevron-right")}
              class="size-4"
            />
          </button>
          <span
            :if={!node.expandable?}
            aria-hidden="true"
            class="size-7 shrink-0"
          />
          <.link
            id={selection_link_id(node)}
            patch={selection_path(node, @include_children?)}
            phx-hook={
              node.kind == :project && node.project.primary_directory &&
                "TaskmanWeb.WorkspaceNavigation.ProjectDirectoryPopover"
            }
            aria-current={node.selected? && "page"}
            aria-label={"Select #{node_label(node)}"}
            aria-describedby={
              node.kind == :project && node.project.primary_directory &&
                "project-directory-#{node.project.id}"
            }
            class="group/project-link relative flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-1 text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-indigo-400/50"
          >
            <.icon
              name={if(node.kind == :project, do: "hero-folder", else: "hero-list-bullet")}
              class="size-4 shrink-0 text-indigo-300"
            />
            <span class="min-w-0 flex-1 truncate">{node_label(node)}</span>
            <span
              :if={node.kind == :project && node.project.primary_directory}
              id={"project-directory-#{node.project.id}"}
              role="tooltip"
              data-project-directory-popover
              class="pointer-events-auto invisible absolute left-1 top-full z-40 mt-2 w-max max-w-64 translate-y-1 break-all rounded-lg border border-slate-700 bg-slate-900/95 px-2.5 py-2 text-left font-mono text-[0.6875rem] font-normal leading-4 text-slate-300 opacity-0 shadow-xl shadow-black/30 transition duration-150 group-hover/project-link:visible group-hover/project-link:translate-y-0 group-hover/project-link:opacity-100 group-focus-within/project-link:visible group-focus-within/project-link:translate-y-0 group-focus-within/project-link:opacity-100"
            >
              {node.project.primary_directory}
            </span>
          </.link>
          <button
            :if={node.kind == :project}
            id={"add-list-project-#{node.project.id}"}
            type="button"
            phx-click="open_list_form"
            phx-value-kind="new"
            phx-value-parent-id=""
            phx-value-project-id={node.project.id}
            aria-label={"Add List to #{node.project.name}"}
            class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 opacity-100 transition hover:bg-white/10 hover:text-white focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-indigo-400/40 group-hover:opacity-100 pointer-fine:opacity-0"
          >
            <.icon name="hero-plus" class="size-4" />
          </button>
          <button
            :if={node.kind == :list}
            id={"add-child-list-#{node.task_list.id}"}
            type="button"
            phx-click="open_list_form"
            phx-value-kind="new"
            phx-value-parent-id={node.task_list.id}
            phx-value-project-id={node.project.id}
            aria-label={"Add child List to #{node.task_list.name}"}
            class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 opacity-100 transition hover:bg-white/10 hover:text-white focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-indigo-400/40 group-hover:opacity-100 pointer-fine:opacity-0"
          >
            <.icon name="hero-plus" class="size-4" />
          </button>
          <button
            :if={node.kind == :list}
            id={"rename-list-#{node.task_list.id}"}
            type="button"
            phx-click="open_list_form"
            phx-value-kind="rename"
            phx-value-list-id={node.task_list.id}
            phx-value-project-id={node.project.id}
            aria-label={"Rename #{node.task_list.name}"}
            class="grid size-7 shrink-0 place-items-center rounded-lg text-slate-500 opacity-100 transition hover:bg-white/10 hover:text-white focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-indigo-400/40 group-hover:opacity-100 pointer-fine:opacity-0"
          >
            <.icon name="hero-pencil-square" class="size-4" />
          </button>
        </div>
        <.list_form
          :if={ListEdit.active_for?(@list_edit, node)}
          list_edit={@list_edit}
        />
      </div>
    </nav>
    <script :type={Phoenix.LiveView.ColocatedHook} name=".ProjectDirectoryPopover">
      export default {
        mounted() {
          this.tooltip = document.getElementById(this.el.getAttribute("aria-describedby"))
          this.showTooltip = () => {
            if (this.tooltip) this.tooltip.hidden = false
          }
          this.dismissTooltip = event => {
            if (event.key !== "Escape" || !this.tooltip) return

            event.preventDefault()
            event.stopPropagation()
            this.tooltip.hidden = true
          }

          this.el.addEventListener("mouseenter", this.showTooltip)
          this.el.addEventListener("focus", this.showTooltip)
          this.el.addEventListener("keydown", this.dismissTooltip)
        },

        destroyed() {
          this.el.removeEventListener("mouseenter", this.showTooltip)
          this.el.removeEventListener("focus", this.showTooltip)
          this.el.removeEventListener("keydown", this.dismissTooltip)
        }
      }
    </script>
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

  defp node_id(%NavigationNode{kind: :project, project: %Project{id: id}}), do: id
  defp node_id(%NavigationNode{kind: :list, task_list: %TaskList{id: id}}), do: id

  defp node_label(%NavigationNode{kind: :project, project: %Project{name: name}}), do: name
  defp node_label(%NavigationNode{kind: :list, task_list: %TaskList{name: name}}), do: name

  defp selection_link_id(%NavigationNode{kind: kind} = node),
    do: "select-#{kind}-#{node_id(node)}"

  defp toggle_id(%NavigationNode{kind: kind} = node), do: "toggle-#{kind}-#{node_id(node)}"

  defp toggle_label(%NavigationNode{} = node) do
    if node.expanded?, do: "Collapse #{node_label(node)}", else: "Expand #{node_label(node)}"
  end

  defp selection_path(
         %NavigationNode{kind: :project, project: %Project{id: project_id}},
         include_children?
       ),
       do: append_include_children(~p"/projects/#{project_id}", include_children?)

  defp selection_path(
         %NavigationNode{
           kind: :list,
           project: %Project{id: project_id},
           task_list: %TaskList{id: list_id}
         },
         include_children?
       ),
       do:
         append_include_children(~p"/projects/#{project_id}/lists/#{list_id}", include_children?)

  defp append_include_children(path, true), do: path <> "?include_children=true"
  defp append_include_children(path, false), do: path
end
