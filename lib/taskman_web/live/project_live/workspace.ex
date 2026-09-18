defmodule TaskmanWeb.ProjectLive.Workspace do
  import Phoenix.Component, only: [assign: 3, update: 3, to_form: 1]
  import Phoenix.LiveView, only: [connected?: 1, stream: 4, push_patch: 2]

  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Lists
  alias Taskman.Projects
  alias TaskmanWeb.ProjectLive.{ListEdit, Paths}
  alias TaskmanWeb.ProjectLive.Tasks.{Listing, Movement}
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState
  alias __MODULE__, as: Workspace

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project

  defmodule State do
    alias TaskmanWeb.ProjectLive.ListEdit

    defstruct selected_project: nil,
              subscribed_project_id: nil,
              project_not_found?: false,
              selected_list: nil,
              include_children?: false,
              location_not_found?: false,
              location_path: [],
              project_form: nil,
              expanded_node_ids: MapSet.new(),
              list_edit: ListEdit.empty()

    @type t :: %__MODULE__{
            selected_project: Project.t() | nil,
            subscribed_project_id: pos_integer() | nil,
            project_not_found?: boolean(),
            selected_list: TaskList.t() | nil,
            include_children?: boolean(),
            location_not_found?: boolean(),
            location_path: [TaskList.t()],
            project_form: Phoenix.HTML.Form.t() | nil,
            expanded_node_ids: MapSet.t(),
            list_edit: ListEdit.t()
          }

    @spec new(Phoenix.HTML.Form.t()) :: t()
    def new(project_form), do: %__MODULE__{project_form: project_form}

    @spec select_location(t(), Project.t(), TaskList.t() | nil, boolean(), [TaskList.t()]) :: t()
    def select_location(state, project, task_list, include_children?, location_path) do
      %{
        state
        | selected_project: project,
          selected_list: task_list,
          project_not_found?: false,
          include_children?: include_children?,
          location_not_found?: false,
          location_path: location_path
      }
    end

    @spec project_not_found(t()) :: t()
    def project_not_found(state) do
      %{
        state
        | selected_project: nil,
          selected_list: nil,
          project_not_found?: true,
          location_not_found?: false,
          location_path: []
      }
    end

    @spec location_not_found(t(), Project.t()) :: t()
    def location_not_found(state, project) do
      %{
        state
        | selected_project: project,
          selected_list: nil,
          project_not_found?: false,
          location_not_found?: true,
          location_path: []
      }
    end

    @spec toggle_node(t(), term()) :: t()
    def toggle_node(%__MODULE__{expanded_node_ids: expanded_node_ids} = state, identity) do
      expanded_node_ids =
        if MapSet.member?(expanded_node_ids, identity) do
          MapSet.delete(expanded_node_ids, identity)
        else
          MapSet.put(expanded_node_ids, identity)
        end

      %{state | expanded_node_ids: expanded_node_ids}
    end

    @spec put_list_edit(t(), ListEdit.t()) :: t()
    def put_list_edit(state, %ListEdit{} = list_edit), do: %{state | list_edit: list_edit}

    @spec clear_list_edit(t()) :: t()
    def clear_list_edit(%__MODULE__{} = state),
      do: %{state | list_edit: ListEdit.clear(state.list_edit)}

    @spec put_subscription(t(), pos_integer() | nil) :: t()
    def put_subscription(%__MODULE__{} = state, project_id),
      do: %{state | subscribed_project_id: project_id}
  end

  @spec panel_state(State.t()) :: String.t()
  def panel_state(%State{project_not_found?: true}), do: "not-found"
  def panel_state(%State{location_not_found?: true}), do: "location-not-found"
  def panel_state(%State{selected_project: %Project{}}), do: "selected"
  def panel_state(%State{}), do: "no-selection"

  @spec selected_location(State.t()) :: {:project, pos_integer()} | {:list, pos_integer()} | nil
  def selected_location(%State{selected_project: %Project{id: project_id}, selected_list: nil}),
    do: {:project, project_id}

  def selected_location(%State{selected_list: %TaskList{id: list_id}}), do: {:list, list_id}
  def selected_location(%State{}), do: nil

  @events ~w(validate_project save_project toggle_navigation_node open_list_form cancel_list_form validate_list save_list)

  @spec events() :: [String.t()]
  def events, do: @events

  @spec resolve_location(map()) ::
          {:ok, Project.t(), TaskList.t() | nil}
          | {:error, :project_not_found}
          | {:error, :location_not_found, Project.t()}
  def resolve_location(%{"project_id" => project_id} = params) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        case Map.fetch(params, "list_id") do
          :error ->
            {:ok, project, nil}

          {:ok, list_id} ->
            case Lists.get_list_for_project(project, list_id) do
              %TaskList{} = task_list -> {:ok, project, task_list}
              nil -> {:error, :location_not_found, project}
            end
        end

      nil ->
        {:error, :project_not_found}
    end
  end

  def resolve_location(_params), do: {:error, :project_not_found}

  @spec location_path(Project.t(), TaskList.t() | nil) :: [TaskList.t()]
  def location_path(_project, nil), do: []

  def location_path(project, %TaskList{} = task_list) do
    project
    |> Lists.list_lists_for_project()
    |> Lists.path_for(task_list)
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("validate_project", %{"project" => project_params}, socket) do
    form =
      %Project{}
      |> Projects.change_project(project_params)
      |> Map.put(:action, :validate)
      |> to_form()

    {:noreply, update_workspace(socket, &%{&1 | project_form: form})}
  end

  def handle_event("save_project", %{"project" => project_params}, socket) do
    case Projects.create_project(project_params) do
      {:ok, project} ->
        {:noreply,
         socket
         |> update_workspace(&%{&1 | project_form: project_form(%Project{})})
         |> push_patch(to: Paths.browse_path(project, nil, false))}

      {:error, changeset} ->
        {:noreply, update_workspace(socket, &%{&1 | project_form: to_form(changeset)})}
    end
  end

  def handle_event("toggle_navigation_node", params, socket) when is_map(params) do
    case navigation_identity(params) do
      nil ->
        {:noreply, socket}

      identity ->
        {:noreply,
         socket
         |> update_workspace(&Workspace.State.toggle_node(&1, identity))
         |> refresh()}
    end
  end

  def handle_event("open_list_form", params, socket) do
    case action_project(socket, params) do
      %Project{} = project ->
        case Map.get(params, "kind") do
          "new" -> open_new_list_form(socket, project, parent_id_param(params))
          "rename" -> open_rename_list_form(socket, project, list_id_param(params))
          _other -> {:noreply, socket}
        end

      _project_not_selected ->
        {:noreply, socket}
    end
  end

  def handle_event("cancel_list_form", _params, socket) do
    {:noreply,
     socket
     |> clear_list_edit()
     |> refresh()}
  end

  def handle_event("validate_list", %{"list" => list_params}, socket) do
    case ListEdit.validate(socket.assigns.workspace.list_edit, list_params) do
      {:ok, list_edit} ->
        {:noreply,
         socket
         |> update_workspace(&Workspace.State.put_list_edit(&1, list_edit))
         |> refresh()}

      {:error, :not_found} ->
        {:noreply, socket}
    end
  end

  def handle_event("save_list", %{"list" => list_params}, socket) do
    case {socket.assigns.workspace.list_edit, ListEdit.target(socket.assigns.workspace.list_edit)} do
      {%ListEdit{project: %Project{} = project}, {:ok, {:new, parent}, _task_list}} ->
        case Lists.create_list(project, parent, list_params) do
          {:ok, _task_list} ->
            expanded_node_ids =
              MapSet.put(
                socket.assigns.workspace.expanded_node_ids,
                if(parent, do: {:list, parent.id}, else: {:project, project.id})
              )

            {:noreply,
             socket
             |> update_workspace(&%{&1 | expanded_node_ids: expanded_node_ids})
             |> clear_list_edit()
             |> refresh()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_edit_error(socket, changeset)}

          {:error, :not_found} ->
            {:noreply, socket}
        end

      {%ListEdit{project: %Project{} = project}, {:ok, {:rename, current_list}, _task_list}} ->
        case Lists.rename_list(project, current_list, list_params) do
          {:ok, renamed_list} ->
            selected_list =
              case socket.assigns.workspace.selected_list do
                %TaskList{id: id} when id == renamed_list.id -> renamed_list
                selected -> selected
              end

            selected_project = socket.assigns.workspace.selected_project

            selected_location_path =
              case {selected_project, selected_list} do
                {%Project{} = current_project, %TaskList{}} ->
                  location_path(current_project, selected_list)

                _no_selected_list ->
                  []
              end

            {:noreply,
             socket
             |> update_workspace(
               &%{&1 | selected_list: selected_list, location_path: selected_location_path}
             )
             |> clear_list_edit()
             |> refresh()
             |> Listing.refresh()
             |> Movement.refresh()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_edit_error(socket, changeset)}

          {:error, :not_found} ->
            {:noreply, socket}
        end

      _invalid_form ->
        {:noreply, socket}
    end
  end

  defp navigation_identity(%{"kind" => "project", "id" => id} = params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id", id))

    with {:ok, node_id} <- parse_navigation_identity(id),
         {:ok, owning_project_id} <- parse_navigation_identity(project_id),
         true <- node_id == owning_project_id,
         %Project{} <- Projects.get_project(node_id) do
      {:project, node_id}
    else
      _invalid -> nil
    end
  end

  defp navigation_identity(%{"kind" => "list", "id" => id} = params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    with {:ok, list_id} <- parse_navigation_identity(id),
         {:ok, project_id} <- parse_navigation_identity(project_id),
         %Project{} = project <- Projects.get_project(project_id),
         %TaskList{} <- Lists.get_list_for_project(project, list_id) do
      {:list, list_id}
    else
      _invalid -> nil
    end
  end

  defp navigation_identity(_params), do: nil

  defp parse_navigation_identity(id) do
    case Integer.parse(to_string(id)) do
      {parsed, ""} when parsed > 0 -> {:ok, parsed}
      _invalid -> :error
    end
  end

  defp parent_id_param(params), do: Map.get(params, "parent-id", Map.get(params, "parent_id", ""))
  defp list_id_param(params), do: Map.get(params, "list-id", Map.get(params, "list_id"))

  defp action_project(socket, params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    case project_id do
      nil -> socket.assigns.workspace.selected_project
      "" -> nil
      id -> Projects.get_project(id)
    end
  end

  defp open_new_list_form(socket, project, parent_id) do
    case resolve_parent_list(project, parent_id) do
      {:ok, parent} ->
        {:noreply,
         socket
         |> update_workspace(
           &Workspace.State.put_list_edit(&1, ListEdit.open_new(project, parent))
         )
         |> refresh()}

      :error ->
        {:noreply, socket}
    end
  end

  defp open_rename_list_form(socket, project, list_id) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list ->
        {:noreply,
         socket
         |> update_workspace(
           &Workspace.State.put_list_edit(&1, ListEdit.open_rename(project, task_list))
         )
         |> refresh()}

      nil ->
        {:noreply, socket}
    end
  end

  defp resolve_parent_list(_project, parent_id) when parent_id in [nil, ""], do: {:ok, nil}

  defp resolve_parent_list(project, parent_id) do
    case Lists.get_list_for_project(project, parent_id) do
      %TaskList{} = parent -> {:ok, parent}
      nil -> :error
    end
  end

  defp assign_list_edit_error(socket, changeset) do
    socket
    |> update_workspace(
      &Workspace.State.put_list_edit(&1, ListEdit.put_error(&1.list_edit, changeset))
    )
    |> refresh()
  end

  defp clear_list_edit(socket) do
    update_workspace(socket, &Workspace.State.clear_list_edit/1)
  end

  @doc "Refreshes the navigation stream from a complete workspace snapshot."
  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(socket) do
    {projects, lists_by_project} = workspace_snapshot()
    stream_navigation(socket, projects, lists_by_project)
  end

  @spec subscribe(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def subscribe(socket) do
    if connected?(socket) do
      _ = ChangeNotifications.subscribe_workspace()
    end

    socket
  end

  defp workspace_snapshot do
    projects = Projects.list_projects()

    lists_by_project =
      Map.new(projects, fn project ->
        {project.id, Lists.list_lists_for_project(project)}
      end)

    {projects, lists_by_project}
  end

  defp stream_navigation(socket, projects, lists_by_project) do
    nodes =
      Lists.navigation_nodes(
        projects,
        lists_by_project,
        Workspace.selected_location(socket.assigns.workspace),
        socket.assigns.workspace.expanded_node_ids
      )

    stream(socket, :navigation_nodes, nodes, reset: true)
  end

  @doc """
  Reconciles a validated Project/List notification, updating only workspace state and streams.

  The outcome describes the selected location so the coordinator can order downstream refreshes.
  `:unchanged` means no selected-location refresh is required; navigation may still change.
  """
  @spec reconcile(Phoenix.LiveView.Socket.t(), Event.t()) ::
          {Phoenix.LiveView.Socket.t(),
           :unchanged | {:location_changed, [TaskList.t()]} | {:location_missing, [TaskList.t()]}}
  def reconcile(socket, %Event{entity: :project}) do
    {projects, lists_by_project} = workspace_snapshot()

    socket =
      socket
      |> stream(:projects, projects, reset: true)
      |> stream_navigation(projects, lists_by_project)

    {socket, :unchanged}
  end

  def reconcile(socket, %Event{entity: :list, project_id: project_id}) do
    {projects, lists_by_project} = workspace_snapshot()

    socket =
      socket
      |> update_workspace(
        &Workspace.State.put_list_edit(
          &1,
          ListEdit.reconcile(&1.list_edit, projects, lists_by_project)
        )
      )
      |> stream(:projects, projects, reset: true)

    {socket, outcome} =
      reconcile_selected_location(socket, project_id, projects, lists_by_project)

    {stream_navigation(socket, projects, lists_by_project), outcome}
  end

  defp reconcile_selected_location(
         socket,
         event_project_id,
         projects,
         lists_by_project
       ) do
    if project_id(socket.assigns.workspace.selected_project) == event_project_id do
      case Enum.find(projects, &(&1.id == event_project_id)) do
        %Project{} = project ->
          task_lists = Map.get(lists_by_project, event_project_id, [])
          previous_list = socket.assigns.workspace.selected_list
          selected_list = canonical_selected_list(previous_list, task_lists)
          location_not_found? = match?(%TaskList{}, previous_list) and is_nil(selected_list)
          location_path = Lists.path_for(task_lists, selected_list)

          workspace =
            if location_not_found? do
              Workspace.State.location_not_found(socket.assigns.workspace, project)
            else
              Workspace.State.select_location(
                socket.assigns.workspace,
                project,
                selected_list,
                socket.assigns.workspace.include_children?,
                location_path
              )
            end

          outcome =
            if location_not_found?,
              do: {:location_missing, task_lists},
              else: {:location_changed, task_lists}

          {assign(socket, :workspace, workspace), outcome}

        nil ->
          {socket, :unchanged}
      end
    else
      {socket, :unchanged}
    end
  end

  defp canonical_selected_list(nil, _task_lists), do: nil

  defp canonical_selected_list(%TaskList{id: list_id}, task_lists) do
    Enum.find(task_lists, &(&1.id == list_id))
  end

  @doc "Applies route location state, switches subscriptions, and refreshes navigation."
  @spec assign_location(
          Phoenix.LiveView.Socket.t(),
          Project.t() | nil,
          TaskList.t() | nil,
          boolean(),
          boolean(),
          boolean(),
          [TaskList.t()]
        ) :: Phoenix.LiveView.Socket.t()
  def assign_location(
        socket,
        selected_project,
        selected_list,
        project_not_found?,
        location_not_found?,
        include_children?,
        location_path
      ) do
    workspace =
      socket.assigns.workspace
      |> workspace_location_state(
        selected_project,
        selected_list,
        project_not_found?,
        location_not_found?,
        include_children?,
        location_path
      )
      |> Workspace.State.clear_list_edit()

    socket
    |> assign(:workspace, workspace)
    |> sync_project_task_subscription(selected_project)
    |> update(:listing, &ListingState.close_filter/1)
    |> stream(:projects, Projects.list_projects(), reset: true)
    |> refresh()
  end

  defp workspace_location_state(
         workspace,
         _selected_project,
         _selected_list,
         true,
         _location_not_found?,
         include_children?,
         _location_path
       ) do
    workspace
    |> Workspace.State.project_not_found()
    |> Map.put(:include_children?, include_children?)
  end

  defp workspace_location_state(
         workspace,
         selected_project,
         _selected_list,
         _project_not_found?,
         true,
         include_children?,
         _location_path
       ) do
    workspace
    |> Workspace.State.location_not_found(selected_project)
    |> Map.put(:include_children?, include_children?)
  end

  defp workspace_location_state(
         workspace,
         selected_project,
         selected_list,
         _project_not_found?,
         _location_not_found?,
         include_children?,
         location_path
       ),
       do:
         Workspace.State.select_location(
           workspace,
           selected_project,
           selected_list,
           include_children?,
           location_path
         )

  defp sync_project_task_subscription(socket, desired_project) do
    desired_project_id = project_id(desired_project)
    subscribed_project_id = socket.assigns.workspace.subscribed_project_id

    cond do
      not connected?(socket) ->
        socket

      desired_project_id == subscribed_project_id ->
        socket

      true ->
        if is_integer(subscribed_project_id) do
          ChangeNotifications.unsubscribe_project(subscribed_project_id)
        end

        case desired_project_id do
          nil ->
            update_workspace(socket, &Workspace.State.put_subscription(&1, nil))

          project_id ->
            case ChangeNotifications.subscribe_project(project_id) do
              :ok ->
                update_workspace(socket, &Workspace.State.put_subscription(&1, project_id))

              {:error, _reason} ->
                update_workspace(socket, &Workspace.State.put_subscription(&1, nil))
            end
        end
    end
  end

  defp project_id(%Project{id: project_id}) when is_integer(project_id) and project_id > 0,
    do: project_id

  defp project_id(_project), do: nil

  defp update_workspace(socket, transition) do
    assign(socket, :workspace, transition.(socket.assigns.workspace))
  end

  @spec project_form(Project.t()) :: Phoenix.HTML.Form.t()
  def project_form(project), do: to_form(Projects.change_project(project))
end
