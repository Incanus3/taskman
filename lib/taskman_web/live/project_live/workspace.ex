defmodule TaskmanWeb.ProjectLive.Workspace do
  import Phoenix.Component, only: [assign: 3, update: 3]
  import Phoenix.LiveView, only: [connected?: 1, stream: 4, push_patch: 2]

  alias Taskman.{ChangeNotifications, Lists, Projects, Tasks}
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks.{Status, Task}

  alias TaskmanWeb.ProjectLive.{
    ListEdit,
    ListEditing,
    Locations,
    Paths,
    ProjectEdit,
    ProjectEditing
  }

  alias TaskmanWeb.ProjectLive.Tasks.{Listing, Movement}
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState
  alias __MODULE__, as: Workspace

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project

  defmodule State do
    alias TaskmanWeb.ProjectLive.{ListEdit, Locations, ProjectEdit}

    defstruct locations: Locations.new(),
              selected_project: nil,
              subscribed_project_id: nil,
              project_not_found?: false,
              selected_list: nil,
              include_children?: false,
              location_not_found?: false,
              location_path: [],
              project_selector_open?: false,
              mobile_sidebar_open?: false,
              project_edit: ProjectEdit.empty(),
              expanded_node_ids: MapSet.new(),
              collapsed_node_ids: MapSet.new(),
              list_edit: ListEdit.empty()

    @type t :: %__MODULE__{
            locations: Locations.t(),
            selected_project: Project.t() | nil,
            subscribed_project_id: pos_integer() | nil,
            project_not_found?: boolean(),
            selected_list: TaskList.t() | nil,
            include_children?: boolean(),
            location_not_found?: boolean(),
            location_path: [TaskList.t()],
            project_selector_open?: boolean(),
            mobile_sidebar_open?: boolean(),
            project_edit: ProjectEdit.t(),
            expanded_node_ids: MapSet.t(),
            collapsed_node_ids: MapSet.t(),
            list_edit: ListEdit.t()
          }

    @spec new() :: t()
    def new, do: %__MODULE__{}

    @spec select_location(t(), Project.t() | nil, TaskList.t() | nil, boolean(), [TaskList.t()]) ::
            t()
    def select_location(state, project, task_list, include_children?, location_path) do
      same_path? =
        (state.selected_project && state.selected_project.id) == (project && project.id) &&
          Enum.map(state.location_path, & &1.id) == Enum.map(location_path, & &1.id)

      collapsed_node_ids =
        if same_path? do
          state.collapsed_node_ids
        else
          required_ids =
            location_path
            |> Enum.drop(-1)
            |> Enum.map(&{:list, &1.id})
            |> MapSet.new()

          MapSet.difference(state.collapsed_node_ids, required_ids)
        end

      %{
        state
        | selected_project: project,
          selected_list: task_list,
          project_not_found?: false,
          include_children?: include_children?,
          location_not_found?: false,
          location_path: location_path,
          collapsed_node_ids: collapsed_node_ids
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
    def toggle_node(%__MODULE__{} = state, identity) do
      forced_open? = forced_open_ancestor?(state, identity)

      expanded? =
        MapSet.member?(state.expanded_node_ids, identity) ||
          (forced_open? && !MapSet.member?(state.collapsed_node_ids, identity))

      if expanded? do
        collapsed_node_ids =
          if forced_open?,
            do: MapSet.put(state.collapsed_node_ids, identity),
            else: MapSet.delete(state.collapsed_node_ids, identity)

        %{
          state
          | expanded_node_ids: MapSet.delete(state.expanded_node_ids, identity),
            collapsed_node_ids: collapsed_node_ids
        }
      else
        %{
          state
          | expanded_node_ids: MapSet.put(state.expanded_node_ids, identity),
            collapsed_node_ids: MapSet.delete(state.collapsed_node_ids, identity)
        }
      end
    end

    defp forced_open_ancestor?(state, {:list, list_id}) do
      state.location_path
      |> Enum.drop(-1)
      |> Enum.any?(&(&1.id == list_id))
    end

    defp forced_open_ancestor?(_state, _identity), do: false

    @spec put_list_edit(t(), ListEdit.t()) :: t()
    def put_list_edit(state, %ListEdit{} = list_edit), do: %{state | list_edit: list_edit}

    @spec clear_list_edit(t()) :: t()
    def clear_list_edit(%__MODULE__{} = state),
      do: %{state | list_edit: ListEdit.clear(state.list_edit)}

    @spec put_subscription(t(), pos_integer() | nil) :: t()
    def put_subscription(%__MODULE__{} = state, project_id),
      do: %{state | subscribed_project_id: project_id}

    def put_project_edit(state, %ProjectEdit{} = edit), do: %{state | project_edit: edit}
    def close_project_edit(state), do: %{state | project_edit: ProjectEdit.empty()}

    def toggle_project_selector(state),
      do: %{state | project_selector_open?: !state.project_selector_open?}

    def close_project_selector(state), do: %{state | project_selector_open?: false}

    def toggle_mobile_sidebar(%__MODULE__{mobile_sidebar_open?: true} = state),
      do: close_mobile_sidebar(state)

    def toggle_mobile_sidebar(%__MODULE__{} = state),
      do: %{state | mobile_sidebar_open?: true}

    def close_mobile_sidebar(%__MODULE__{} = state),
      do: %{state | mobile_sidebar_open?: false, project_selector_open?: false}
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

  @events ~w(resnapshot_task_locations toggle_project_selector close_project_selector toggle_mobile_sidebar close_mobile_sidebar open_project_new open_project_edit cancel_project_edit select_project_color validate_project save_project toggle_navigation_node open_list_form cancel_list_form validate_list save_list)

  @spec events() :: [String.t()]
  def events, do: @events

  @spec resolve_location(map()) ::
          {:ok, Project.t(), TaskList.t() | nil}
          | {:error, :project_not_found}
          | {:error, :location_not_found, Project.t()}
  def resolve_location(%{"project_id" => project_id} = params) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        resolve_project_location(project, Map.fetch(params, "list_id"))

      nil ->
        {:error, :project_not_found}
    end
  end

  def resolve_location(_params), do: {:error, :project_not_found}

  @spec remembered_project(term()) :: {:ok, Project.t()} | :stale
  def remembered_project(id) when is_binary(id) do
    with true <- Regex.match?(~r/^[1-9][0-9]*$/, id),
         {project_id, ""} <- Integer.parse(id),
         true <- project_id <= 9_223_372_036_854_775_807,
         %Project{} = project <- Projects.get_project(project_id) do
      {:ok, project}
    else
      _ -> :stale
    end
  end

  def remembered_project(_id), do: :stale

  @spec location_path(Phoenix.LiveView.Socket.t(), Project.t(), TaskList.t() | nil) :: [
          TaskList.t()
        ]
  def location_path(socket, project, task_list) do
    socket.assigns.workspace.locations
    |> Locations.lists(project.id)
    |> Lists.path_for(task_list)
  end

  @doc "Loads one Project's locations and availability only if it has not been bootstrapped."
  @spec ensure_locations(Phoenix.LiveView.Socket.t(), Project.t()) :: Phoenix.LiveView.Socket.t()
  def ensure_locations(socket, %Project{} = project) do
    if Locations.loaded?(socket.assigns.workspace.locations, project.id),
      do: socket,
      else: load_locations(socket, project)
  end

  @doc "Merges a fresh active-Project snapshot with observations retained by this page."
  @spec resnapshot_locations(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def resnapshot_locations(
        %{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket
      ),
      do: load_locations(socket, project)

  def resnapshot_locations(socket), do: socket

  @doc "Observes a successful persisted local result without reading Tasks."
  @spec observe_task(Phoenix.LiveView.Socket.t(), Task.t()) :: Phoenix.LiveView.Socket.t()
  def observe_task(socket, %Task{id: id, project_id: project_id, list_id: list_id, status: status})
      when is_integer(id) and id > 0 do
    observe_snapshot(socket, project_id, list_id, status)
  end

  def observe_task(socket, _task), do: socket

  @doc "Merges relevant persisted event snapshots; malformed snapshots never cause a lookup."
  @spec observe_task_event(Phoenix.LiveView.Socket.t(), Event.t()) :: Phoenix.LiveView.Socket.t()
  def observe_task_event(socket, %Event{
        entity: :task,
        operation: operation,
        entity_id: id,
        project_id: project_id,
        list_id: list_id,
        status: status,
        fields: fields,
        lock_version: version
      })
      when operation in [:created, :updated, :moved] and is_integer(id) and id > 0 and
             is_list(fields) do
    observe_valid_task_event(socket, operation, project_id, list_id, status, fields, version)
  end

  def observe_task_event(socket, _event), do: socket

  defp observe_valid_task_event(socket, operation, project_id, list_id, status, fields, version)
       when is_nil(version) or (is_integer(version) and version >= 0) do
    relevant? = operation in [:created, :moved] or Enum.any?(fields, &(&1 in [:status, :list_id]))

    if relevant? and Enum.all?(fields, &is_atom/1),
      do: observe_snapshot(socket, project_id, list_id, status),
      else: socket
  end

  defp observe_valid_task_event(
         socket,
         _operation,
         _project_id,
         _list_id,
         _status,
         _fields,
         _version
       ),
       do: socket

  defp observe_snapshot(socket, project_id, list_id, status) do
    workspace = socket.assigns.workspace

    if is_integer(project_id) and project_id > 0 and
         project_id(workspace.selected_project) == project_id and status in Status.values() and
         (is_nil(list_id) or (is_integer(list_id) and list_id > 0)) do
      locations = Locations.observe(workspace.locations, project_id, list_id, status)

      if locations == workspace.locations,
        do: socket,
        else: update_workspace(socket, &%{&1 | locations: locations})
    else
      socket
    end
  end

  @doc "Returns the selected listing's prepared direct or subtree choices."
  def available_statuses(%State{selected_project: %Project{id: id}} = workspace) do
    list_id = workspace.selected_list && workspace.selected_list.id
    scope = if workspace.include_children?, do: :subtree, else: :direct
    Locations.available(workspace.locations, id, list_id, scope)
  end

  def available_statuses(%State{}), do: Status.default_values()

  @doc "Refreshes active-Project List topology without discovering Task states."
  @spec refresh_locations(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh_locations(
        %{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket
      ),
      do: reconcile_locations(socket, project, Lists.list_lists_for_project(project))

  def refresh_locations(socket), do: socket

  @doc "Returns active-Project canonical Lists without reading persistence."
  @spec lists(Phoenix.LiveView.Socket.t()) :: [TaskList.t()]
  def lists(%{
        assigns: %{workspace: %{selected_project: %Project{id: id}, locations: locations}}
      }),
      do: Locations.lists(locations, id)

  def lists(_socket), do: []

  defp load_locations(socket, project) do
    socket = sync_project_task_subscription(socket, project)
    task_lists = Lists.list_lists_for_project(project)
    observed = Tasks.additional_states_by_location(project)

    update_workspace(
      socket,
      &%{&1 | locations: Locations.load(&1.locations, project.id, task_lists, observed)}
    )
  end

  defp reconcile_locations(socket, project, task_lists) do
    update_workspace(
      socket,
      &%{&1 | locations: Locations.reconcile_lists(&1.locations, project.id, task_lists)}
    )
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("resnapshot_task_locations", %{"project_id" => supplied_id}, socket) do
    workspace = socket.assigns.workspace
    selected_id = project_id(workspace.selected_project)

    valid? =
      is_integer(selected_id) and workspace.subscribed_project_id == selected_id and
        supplied_id in [selected_id, to_string(selected_id)]

    {:noreply, if(valid?, do: resnapshot_locations(socket), else: socket)}
  end

  def handle_event("resnapshot_task_locations", _params, socket), do: {:noreply, socket}

  def handle_event("toggle_project_selector", _params, socket) do
    socket = update_workspace(socket, &State.toggle_project_selector/1)

    socket =
      if socket.assigns.workspace.project_selector_open?,
        do: stream(socket, :projects, Projects.list_projects(), reset: true),
        else: socket

    {:noreply, socket}
  end

  def handle_event("close_project_selector", _params, socket) do
    {:noreply, update_workspace(socket, &State.close_project_selector/1)}
  end

  def handle_event("toggle_mobile_sidebar", _params, socket) do
    {:noreply, update_workspace(socket, &State.toggle_mobile_sidebar/1)}
  end

  def handle_event("close_mobile_sidebar", _params, socket) do
    {:noreply, update_workspace(socket, &State.close_mobile_sidebar/1)}
  end

  def handle_event("open_project_new", _params, socket) do
    {:noreply,
     update_workspace(socket, fn state ->
       state |> State.close_project_selector() |> State.put_project_edit(ProjectEdit.open_new())
     end)}
  end

  def handle_event("open_project_edit", _params, socket) do
    case socket.assigns.workspace.selected_project do
      %Project{} = project ->
        {:noreply,
         update_workspace(socket, fn state ->
           state
           |> State.close_project_selector()
           |> State.put_project_edit(ProjectEdit.open_edit(project))
         end)}

      nil ->
        {:noreply, socket}
    end
  end

  def handle_event("cancel_project_edit", _params, socket) do
    {:noreply, update_workspace(socket, &State.close_project_edit/1)}
  end

  def handle_event("select_project_color", %{"color" => color}, socket) do
    edit = socket.assigns.workspace.project_edit

    case {ProjectEdit.target(edit), edit.form, color} do
      {{:ok, _mode, _project}, %Phoenix.HTML.Form{} = form, color}
      when color in ~w(6366F1 3B82F6 06B6D4 10B981 F59E0B F97316 F43F5E D946EF) ->
        params = Map.put(form.params || %{}, "color", color)
        put_project_validation(socket, params)

      _invalid ->
        {:noreply, socket}
    end
  end

  def handle_event("validate_project", %{"project" => params}, socket),
    do: put_project_validation(socket, params)

  def handle_event("save_project", %{"project" => params}, socket) do
    socket.assigns.workspace.project_edit
    |> ProjectEditing.save(params)
    |> apply_project_editing(socket)
  end

  def handle_event("toggle_navigation_node", params, socket) when is_map(params) do
    case navigation_identity(params, socket.assigns.workspace.selected_project) do
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
    socket.assigns.workspace.selected_project
    |> ListEditing.open(params)
    |> apply_list_editing(socket)
  end

  def handle_event("cancel_list_form", _params, socket) do
    {:noreply,
     socket
     |> clear_list_edit()
     |> refresh()}
  end

  def handle_event("validate_list", %{"list" => list_params}, socket) do
    socket.assigns.workspace.list_edit
    |> ListEditing.validate(list_params)
    |> apply_list_editing(socket)
  end

  def handle_event("save_list", %{"list" => list_params}, socket) do
    socket.assigns.workspace.list_edit
    |> ListEditing.save(list_params)
    |> apply_list_editing(socket)
  end

  defp apply_list_editing({:changed, list_edit}, socket) do
    {:noreply,
     socket
     |> update_workspace(&Workspace.State.put_list_edit(&1, list_edit))
     |> refresh()}
  end

  defp apply_list_editing(:unchanged, socket), do: {:noreply, socket}

  defp apply_list_editing({:created, _task_list, parent}, socket) do
    expanded_node_ids =
      if parent do
        MapSet.put(socket.assigns.workspace.expanded_node_ids, {:list, parent.id})
      else
        socket.assigns.workspace.expanded_node_ids
      end

    {:noreply,
     socket
     |> update_workspace(&%{&1 | expanded_node_ids: expanded_node_ids})
     |> clear_list_edit()
     |> refresh_locations()
     |> Listing.refresh_counts()}
  end

  defp apply_list_editing({:renamed, renamed_list}, socket) do
    socket = refresh_locations(socket)

    selected_list =
      case socket.assigns.workspace.selected_list do
        %TaskList{id: id} when id == renamed_list.id -> renamed_list
        selected -> selected
      end

    selected_project = socket.assigns.workspace.selected_project

    selected_location_path =
      case {selected_project, selected_list} do
        {%Project{} = current_project, %TaskList{}} ->
          location_path(socket, current_project, selected_list)

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
  end

  defp put_project_validation(socket, params) do
    socket.assigns.workspace.project_edit
    |> ProjectEditing.validate(params)
    |> apply_project_editing(socket)
  end

  defp apply_project_editing({:changed, edit}, socket) do
    {:noreply, update_workspace(socket, &State.put_project_edit(&1, edit))}
  end

  defp apply_project_editing({:unchanged, _edit}, socket), do: {:noreply, socket}

  defp apply_project_editing({:created, project}, socket) do
    {:noreply,
     socket
     |> update_workspace(&State.close_project_edit/1)
     |> push_patch(
       to: Paths.browse_path(project, nil, socket.assigns.workspace.include_children?)
     )}
  end

  defp apply_project_editing({:updated, project}, socket) do
    {:noreply,
     update_workspace(socket, fn state ->
       state
       |> State.close_project_edit()
       |> Map.put(:selected_project, project)
     end)
     |> stream(:projects, Projects.list_projects(), reset: true)}
  end

  defp navigation_identity(
         %{"kind" => "list", "id" => id} = params,
         %Project{id: selected_project_id} = project
       ) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    with {:ok, list_id} <- parse_navigation_identity(id),
         {:ok, project_id} <- parse_navigation_identity(project_id),
         true <- project_id == selected_project_id,
         %TaskList{} <- Lists.get_list_for_project(project, list_id) do
      {:list, list_id}
    else
      _invalid -> nil
    end
  end

  defp navigation_identity(_params, _selected_project), do: nil

  defp parse_navigation_identity(id) do
    case Integer.parse(to_string(id)) do
      {parsed, ""} when parsed > 0 -> {:ok, parsed}
      _invalid -> :error
    end
  end

  defp clear_list_edit(socket) do
    update_workspace(socket, &Workspace.State.clear_list_edit/1)
  end

  @doc "Refreshes the active Project's List navigation stream."
  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(%{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket) do
    socket = ensure_locations(socket, project)
    stream_navigation(socket, project, lists(socket))
  end

  def refresh(socket), do: stream(socket, :navigation_nodes, [], reset: true)

  @spec subscribe(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def subscribe(socket) do
    if connected?(socket) do
      _ = ChangeNotifications.subscribe_workspace()
    end

    socket
  end

  defp workspace_snapshot(socket) do
    projects = Projects.list_projects()

    lists_by_project =
      case socket.assigns.workspace.selected_project do
        %Project{} = project -> %{project.id => Lists.list_lists_for_project(project)}
        nil -> %{}
      end

    socket =
      update_workspace(
        socket,
        &%{
          &1
          | locations:
              Locations.retain_projects(
                &1.locations,
                Enum.map(projects, fn project -> project.id end)
              )
        }
      )

    socket =
      case socket.assigns.workspace.selected_project do
        %Project{} = project ->
          reconcile_locations(socket, project, Map.fetch!(lists_by_project, project.id))

        nil ->
          socket
      end

    {socket, projects, lists_by_project}
  end

  defp stream_navigation(socket, project, task_lists) do
    nodes =
      Lists.navigation_nodes(
        project,
        task_lists,
        Tasks.list_ids_with_direct_tasks(project),
        Workspace.selected_location(socket.assigns.workspace),
        socket.assigns.workspace.expanded_node_ids,
        socket.assigns.workspace.collapsed_node_ids
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
    projects = Projects.list_projects()

    workspace = socket.assigns.workspace

    selected_project =
      case workspace.selected_project do
        %Project{id: id} = project -> Enum.find(projects, &(&1.id == id)) || project
        nil -> nil
      end

    workspace = %{
      workspace
      | selected_project: selected_project,
        locations: Locations.retain_projects(workspace.locations, Enum.map(projects, & &1.id)),
        project_edit: ProjectEdit.reconcile(workspace.project_edit, projects)
    }

    socket =
      socket
      |> assign_workspace(workspace)
      |> assign(:projects_empty?, projects == [])
      |> stream(:projects, projects, reset: true)
      |> refresh()

    {socket, :unchanged}
  end

  def reconcile(socket, %Event{entity: :list, project_id: project_id}) do
    {socket, projects, lists_by_project} = workspace_snapshot(socket)

    socket =
      socket
      |> update_workspace(
        &Workspace.State.put_list_edit(
          &1,
          ListEdit.reconcile(&1.list_edit, projects, lists_by_project)
        )
      )
      |> assign(:projects_empty?, projects == [])
      |> stream(:projects, projects, reset: true)

    {socket, outcome} =
      reconcile_selected_location(socket, project_id, projects, lists_by_project)

    {refresh(socket), outcome}
  end

  @doc "Refreshes selected-location authority without requiring a matching notification."
  @spec refresh_selected_location(Phoenix.LiveView.Socket.t()) ::
          {Phoenix.LiveView.Socket.t(),
           :unchanged | {:location_changed, [TaskList.t()]} | {:location_missing, [TaskList.t()]}}
  def refresh_selected_location(socket) do
    {socket, projects, lists_by_project} = workspace_snapshot(socket)
    selected_project_id = project_id(socket.assigns.workspace.selected_project)

    {socket, outcome} =
      reconcile_selected_location(socket, selected_project_id, projects, lists_by_project)

    {refresh(socket), outcome}
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
          reconcile_project_location(socket, event_project_id, project, lists_by_project)

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
    previous_project_id = project_id(socket.assigns.workspace.selected_project)
    socket = sync_project_task_subscription(socket, selected_project)

    socket =
      case selected_project do
        %Project{id: id} = project when id != previous_project_id ->
          load_locations(socket, project)

        %Project{} = project ->
          ensure_locations(socket, project)

        nil ->
          socket
      end

    socket =
      reconcile_route_location(socket, selected_project, selected_list, location_not_found?)

    location_path =
      if selected_project,
        do: location_path(socket, selected_project, selected_list),
        else: location_path

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
      |> Workspace.State.close_project_selector()
      |> Workspace.State.close_mobile_sidebar()

    projects = Projects.list_projects()

    socket
    |> assign_workspace(%{
      workspace
      | locations: Locations.retain_projects(workspace.locations, Enum.map(projects, & &1.id))
    })
    |> update(:listing, &ListingState.close_filter/1)
    |> assign(:projects_empty?, projects == [])
    |> stream(:projects, projects, reset: true)
    |> refresh()
  end

  @doc "Reconciles newly authoritative location metadata through the canonical topology owner."
  @spec reconcile_location(Phoenix.LiveView.Socket.t(), Project.t(), TaskList.t() | nil) ::
          Phoenix.LiveView.Socket.t()
  def reconcile_location(socket, %Project{} = project, task_list) do
    canonical =
      if task_list,
        do:
          Enum.find(
            Locations.lists(socket.assigns.workspace.locations, project.id),
            &(&1.id == task_list.id)
          )

    if canonical != task_list,
      do: reconcile_locations(socket, project, Lists.list_lists_for_project(project)),
      else: socket
  end

  defp reconcile_route_location(socket, %Project{} = project, _task_list, true),
    do: reconcile_locations(socket, project, Lists.list_lists_for_project(project))

  defp reconcile_route_location(socket, %Project{} = project, task_list, false),
    do: reconcile_location(socket, project, task_list)

  defp reconcile_route_location(socket, _project, _list, _missing?), do: socket

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

        subscribe_desired_project(socket, desired_project_id)
    end
  end

  defp subscribe_desired_project(socket, nil),
    do: update_workspace(socket, &Workspace.State.put_subscription(&1, nil))

  defp subscribe_desired_project(socket, project_id) do
    case ChangeNotifications.subscribe_project(project_id) do
      :ok -> update_workspace(socket, &Workspace.State.put_subscription(&1, project_id))
      {:error, _reason} -> update_workspace(socket, &Workspace.State.put_subscription(&1, nil))
    end
  end

  defp project_id(%Project{id: project_id}) when is_integer(project_id) and project_id > 0,
    do: project_id

  defp project_id(_project), do: nil

  defp assign_workspace(socket, workspace) do
    title =
      case workspace.selected_project do
        %Project{name: name} -> "Taskman · " <> name
        nil -> "Taskman"
      end

    socket
    |> assign(:workspace, workspace)
    |> assign(:page_title, title)
  end

  defp update_workspace(socket, transition) do
    assign_workspace(socket, transition.(socket.assigns.workspace))
  end

  defp resolve_project_location(project, :error), do: {:ok, project, nil}

  defp resolve_project_location(project, {:ok, list_id}) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> {:ok, project, task_list}
      nil -> {:error, :location_not_found, project}
    end
  end

  defp reconcile_project_location(socket, event_project_id, project, lists_by_project) do
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

    {assign_workspace(socket, workspace), outcome}
  end
end
