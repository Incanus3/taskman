defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Reconciliation
  alias TaskmanWeb.ProjectLive.Recovery, as: RecoveryWorkflow
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Tasks.Creation
  alias TaskmanWeb.ProjectLive.Tasks.Creation.State, as: CreationState
  alias TaskmanWeb.ProjectLive.Tasks.Listing
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState

  alias TaskmanWeb.ProjectLive.Tasks.{
    Editing,
    Messages,
    Move,
    Movement,
    ParentPicker,
    ParentSelection
  }

  alias TaskmanWeb.Tasks.{Detail, Form, Recovery, Table}
  alias TaskmanWeb.WorkspaceNavigation

  @workspace_events Workspace.events()
  @listing_events Listing.events()
  @creation_events Creation.events()
  @editing_events Editing.events()
  @parent_selection_events ParentSelection.events()
  @movement_events Movement.events()
  @recovery_events RecoveryWorkflow.events()
  @destination_unavailable_with_guidance Messages.destination_unavailable_with_guidance()
  @task_workflow_events @creation_events ++
                          @editing_events ++
                          @parent_selection_events ++ @movement_events

  @impl true
  def mount(_params, _session, socket) do
    socket =
      socket
      |> assign_new(:current_scope, fn -> nil end)
      |> assign_new(:current_user, fn -> nil end)

    socket =
      stream_configure(socket, :tasks,
        dom_id: fn %TaskWithLocation{task: task} -> "tasks-#{task.id}" end
      )

    socket =
      stream_configure(socket, :navigation_nodes,
        dom_id: fn %Taskman.Lists.NavigationNode{dom_id: dom_id} -> dom_id end
      )

    socket =
      socket
      |> assign(:workspace, Workspace.State.new(Workspace.project_form(%Project{})))
      |> assign(:listing, ListingState.new(Task.statuses() -- [:will_not_do]))
      |> assign(:creation, CreationState.empty())
      |> assign(:task_parent_picker, ParentPicker.empty())
      |> assign(:editing, Editing.State.empty())
      |> assign(:task_move, Move.empty())
      |> assign(:recovery, RecoveryWorkflow.State.empty())
      |> stream(:projects, Projects.list_projects())
      |> stream(:tasks, [])
      |> stream(:navigation_nodes, [])
      |> Workspace.refresh()

    {:ok, Workspace.subscribe(socket)}
  end

  @impl true
  def handle_params(params, _uri, socket) do
    if RecoveryWorkflow.State.active?(socket.assigns.recovery) do
      {:noreply, apply_route(params, socket)}
    else
      case Editing.flush(socket) do
        {:ok, socket} -> {:noreply, apply_route(params, socket)}
        {:error, socket} -> {:noreply, Editing.restore_failed_route(socket, params)}
      end
    end
  end

  defp apply_route(params, %{assigns: %{live_action: :index}} = socket) do
    socket =
      socket
      |> clear_task_modal_state()
      |> Workspace.assign_location(nil, nil, false, false, false, [])
      |> Listing.clear()

    if RecoveryWorkflow.State.active?(socket.assigns.recovery),
      do: RecoveryWorkflow.complete_route(socket, params),
      else: socket
  end

  defp apply_route(params, %{assigns: %{live_action: action}} = socket)
       when action in [:show, :new_task, :show_task] do
    include_children? = include_children?(params)

    socket =
      assign(
        socket,
        :listing,
        ListingState.available_sort(socket.assigns.listing, include_children?)
      )

    case Workspace.resolve_location(params) do
      {:ok, project, task_list} ->
        location_path = Workspace.location_path(project, task_list)

        socket =
          socket
          |> clear_modal_state_for_action(action, params)
          |> Workspace.assign_location(
            project,
            task_list,
            false,
            false,
            include_children?,
            location_path
          )
          |> Listing.refresh()

        if RecoveryWorkflow.State.active?(socket.assigns.recovery) do
          RecoveryWorkflow.complete_route(socket, params)
        else
          apply_action(action, params, socket)
        end

      {:error, :project_not_found} ->
        socket =
          socket
          |> clear_task_modal_state()
          |> Workspace.assign_location(nil, nil, true, false, include_children?, [])
          |> Listing.clear()

        if RecoveryWorkflow.State.active?(socket.assigns.recovery),
          do: RecoveryWorkflow.complete_route(socket, params),
          else: socket

      {:error, :location_not_found, project} ->
        socket =
          socket
          |> clear_task_modal_state()
          |> Workspace.assign_location(project, nil, false, true, include_children?, [])
          |> Listing.clear()

        if RecoveryWorkflow.State.active?(socket.assigns.recovery),
          do: RecoveryWorkflow.complete_route(socket, params),
          else: socket
    end
  end

  defp apply_action(:show, _params, socket), do: socket

  defp apply_action(:new_task, params, socket), do: Creation.apply_route(socket, params)

  defp apply_action(:show_task, %{"task_id" => task_id}, socket) do
    project = socket.assigns.workspace.selected_project
    task = Tasks.get_task_for_project(project, task_id)
    socket = Editing.apply_route(socket, project, task)

    if socket.assigns.editing.not_found? do
      socket
    else
      ParentSelection.open_edit(socket, project, task)
    end
  end

  defp include_children?(params), do: Map.get(params, "include_children") == "true"

  @impl true
  def handle_event(event, params, socket) when event in @workspace_events,
    do: Workspace.handle_event(event, params, socket)

  def handle_event(event, params, socket) when event in @listing_events,
    do: Listing.handle_event(event, params, socket)

  def handle_event(event, params, socket) when event in @recovery_events,
    do: RecoveryWorkflow.handle_event(event, params, socket)

  def handle_event(event, _params, %{assigns: %{recovery: %{snapshot: snapshot}}} = socket)
      when event in @task_workflow_events and not is_nil(snapshot),
      do: {:noreply, socket}

  def handle_event(event, params, socket) when event in @editing_events do
    if non_creation_workflow_blocked?(socket),
      do: {:noreply, socket},
      else: Editing.handle_event(event, params, socket)
  end

  def handle_event(event, params, socket) when event in @creation_events do
    if creation_workflow_blocked?(socket),
      do: {:noreply, socket},
      else: Creation.handle_event(event, params, socket)
  end

  def handle_event(event, params, socket) when event in @parent_selection_events do
    if non_creation_workflow_blocked?(socket) and not ordinary_creation_active?(socket),
      do: {:noreply, socket},
      else: ParentSelection.handle_event(event, params, socket)
  end

  def handle_event(event, params, socket) when event in @movement_events do
    if non_creation_workflow_blocked?(socket) do
      {:noreply, socket}
    else
      case Movement.reconcile(socket) do
        {:anchored, socket}
        when event == "submit_move_task" and
               socket.assigns.task_move.error == @destination_unavailable_with_guidance ->
          {:noreply, socket}

        {:anchored, socket} ->
          Movement.handle_event(event, params, socket)

        movement_result ->
          {:noreply, Movement.apply_reconciliation(movement_result)}
      end
    end
  end

  @impl true
  def handle_info(message, socket), do: Reconciliation.handle_info(message, socket)

  defp clear_task_modal_state(socket) do
    socket
    |> clear_transient_task_modal_state()
    |> Editing.clear()
  end

  defp clear_modal_state_for_action(
         %{assigns: %{editing: %{detail_open?: true}}} = socket,
         :show_task,
         params
       ) do
    clear_transient_task_modal_state(socket,
      preserve_editing?: matching_detail_task?(socket, params),
      preserve_movement?: matching_detail_move?(socket, params)
    )
  end

  defp clear_modal_state_for_action(socket, _action, _params), do: clear_task_modal_state(socket)

  defp clear_transient_task_modal_state(socket, opts \\ []) do
    socket =
      if Keyword.get(opts, :preserve_editing?, false),
        do: socket,
        else: update(socket, :editing, &Editing.State.clear_transient/1)

    socket =
      socket
      |> Creation.clear()
      |> ParentSelection.clear()

    if Keyword.get(opts, :preserve_movement?, false), do: socket, else: Movement.clear(socket)
  end

  defp matching_detail_move?(
         %{assigns: %{task_move: %Move{active_task: %{origin: :detail, task_id: task_id}}}},
         %{"task_id" => route_task_id}
       ),
       do: route_task_id == Integer.to_string(task_id)

  defp matching_detail_move?(_socket, _params), do: false

  defp matching_detail_task?(
         %{assigns: %{editing: %{selected_task: %Task{id: task_id}}}},
         %{"task_id" => route_task_id}
       ),
       do: route_task_id == Integer.to_string(task_id)

  defp matching_detail_task?(_socket, _params), do: false

  defp ordinary_creation_active?(socket) do
    socket.assigns.live_action == :new_task and not is_nil(socket.assigns.creation.form) and
      not RecoveryWorkflow.blocked?(socket)
  end

  defp non_creation_workflow_blocked?(socket) do
    RecoveryWorkflow.blocked?(socket) or socket.assigns.workspace.location_not_found? or
      ordinary_creation_active?(socket)
  end

  defp creation_workflow_blocked?(socket) do
    RecoveryWorkflow.blocked?(socket) or
      (socket.assigns.workspace.location_not_found? and not ordinary_creation_active?(socket))
  end
end
