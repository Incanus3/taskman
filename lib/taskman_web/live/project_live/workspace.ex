defmodule TaskmanWeb.ProjectLive.Workspace do
  import Phoenix.Component, only: [assign: 3, update: 3]
  import Phoenix.LiveView, only: [connected?: 1, stream: 4, push_patch: 2]

  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive.{ListEdit, Paths, ProjectEdit}
  alias TaskmanWeb.ProjectLive.Tasks.{Listing, Movement}
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState
  alias __MODULE__, as: Workspace

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project

  defmodule State do
    alias TaskmanWeb.ProjectLive.{ListEdit, ProjectEdit}

    defstruct selected_project: nil,
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

  @events ~w(toggle_project_selector close_project_selector toggle_mobile_sidebar close_mobile_sidebar open_project_new open_project_edit cancel_project_edit select_project_color validate_project save_project toggle_navigation_node open_list_form cancel_list_form validate_list save_list)

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

  @spec location_path(Project.t(), TaskList.t() | nil) :: [TaskList.t()]
  def location_path(_project, nil), do: []

  def location_path(project, %TaskList{} = task_list) do
    project
    |> Lists.list_lists_for_project()
    |> Lists.path_for(task_list)
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
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
    edit = socket.assigns.workspace.project_edit

    case ProjectEdit.target(edit) do
      {:ok, :new, _project} ->
        validate_project_submission(socket, edit, params)

      {:ok, :edit, project} ->
        case Projects.get_project(project.id) do
          nil ->
            unavailable = ProjectEdit.reconcile(edit, [])
            {:noreply, update_workspace(socket, &State.put_project_edit(&1, unavailable))}

          current ->
            validate_project_submission(socket, ProjectEdit.reconcile(edit, [current]), params)
        end

      :error ->
        {:noreply, socket}
    end
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
              if parent do
                MapSet.put(socket.assigns.workspace.expanded_node_ids, {:list, parent.id})
              else
                socket.assigns.workspace.expanded_node_ids
              end

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

  defp validate_project_submission(socket, edit, params) do
    case ProjectEdit.validate(edit, params) do
      {:ok, validated} ->
        cond do
          not validated.changeset.valid? ->
            {:noreply, update_workspace(socket, &State.put_project_edit(&1, validated))}

          true ->
            persist_project_edit(socket, validated)
        end

      {:error, :not_found} ->
        {:noreply, socket}
    end
  end

  defp put_project_validation(socket, params) do
    case ProjectEdit.validate(socket.assigns.workspace.project_edit, params) do
      {:ok, edit} -> {:noreply, update_workspace(socket, &State.put_project_edit(&1, edit))}
      {:error, :not_found} -> {:noreply, socket}
    end
  end

  defp persist_project_edit(socket, edit) do
    attrs = Map.take(edit.changeset.params || %{}, ["name", "description", "icon", "color"])
    attrs = Map.put(attrs, "color", Ecto.Changeset.get_field(edit.changeset, :color))

    case ProjectEdit.target(edit) do
      {:ok, :new, _project} ->
        case Projects.create_project(attrs) do
          {:ok, project} ->
            {:noreply,
             socket
             |> update_workspace(&State.close_project_edit/1)
             |> push_patch(
               to: Paths.browse_path(project, nil, socket.assigns.workspace.include_children?)
             )}

          {:error, changeset} ->
            {:noreply, project_edit_error(socket, edit, changeset)}
        end

      {:ok, :edit, project} ->
        case Projects.get_project(project.id) do
          nil ->
            unavailable = ProjectEdit.reconcile(edit, Projects.list_projects())
            {:noreply, update_workspace(socket, &State.put_project_edit(&1, unavailable))}

          current ->
            case Projects.update_project(current, attrs) do
              {:ok, updated} ->
                {:noreply,
                 update_workspace(socket, fn state ->
                   state
                   |> State.close_project_edit()
                   |> Map.put(:selected_project, updated)
                 end)
                 |> stream(:projects, Projects.list_projects(), reset: true)}

              {:error, changeset} ->
                {:noreply, project_edit_error(socket, edit, changeset)}
            end
        end

      :error ->
        {:noreply, socket}
    end
  end

  defp project_edit_error(socket, edit, changeset) do
    update_workspace(socket, &State.put_project_edit(&1, ProjectEdit.put_error(edit, changeset)))
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

  defp parent_id_param(params), do: Map.get(params, "parent-id", Map.get(params, "parent_id", ""))
  defp list_id_param(params), do: Map.get(params, "list-id", Map.get(params, "list_id"))

  defp action_project(socket, params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    case {socket.assigns.workspace.selected_project, project_id} do
      {%Project{} = project, nil} ->
        project

      {%Project{id: id} = project, requested_id} when requested_id == id ->
        project

      {%Project{id: id} = project, requested_id} ->
        if to_string(requested_id) == Integer.to_string(id), do: project, else: nil

      _other ->
        nil
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

  @doc "Refreshes the active Project's List navigation stream."
  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(%{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket) do
    stream_navigation(socket, project, Lists.list_lists_for_project(project))
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

    {projects, lists_by_project}
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
        project_edit: ProjectEdit.reconcile(workspace.project_edit, projects)
    }

    socket =
      socket
      |> assign(:workspace, workspace)
      |> assign(:projects_empty?, projects == [])
      |> stream(:projects, projects, reset: true)
      |> refresh()

    {socket, :unchanged}
  end

  def reconcile(socket, %Event{entity: :list, project_id: project_id}) do
    {projects, lists_by_project} = workspace_snapshot(socket)

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
      |> Workspace.State.close_project_selector()
      |> Workspace.State.close_mobile_sidebar()

    projects = Projects.list_projects()

    socket
    |> assign(:workspace, workspace)
    |> sync_project_task_subscription(selected_project)
    |> update(:listing, &ListingState.close_filter/1)
    |> assign(:projects_empty?, projects == [])
    |> stream(:projects, projects, reset: true)
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
end
