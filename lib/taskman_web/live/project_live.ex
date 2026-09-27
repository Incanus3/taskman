defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Projects
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.ShareTaskView
  alias TaskmanWeb.ProjectLive.TaskTablePreferences
  alias TaskmanWeb.ProjectLive.Reconciliation
  alias TaskmanWeb.ProjectLive.Recovery, as: RecoveryWorkflow
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Tasks.Creation
  alias TaskmanWeb.ProjectLive.Tasks.Creation.State, as: CreationState
  alias TaskmanWeb.ProjectLive.Tasks.Listing
  alias TaskmanWeb.ProjectLive.Tasks.Listing.State, as: ListingState

  alias TaskmanWeb.ProjectLive.Tasks.{
    Editing,
    CommentDeparture,
    Comments,
    Messages,
    Move,
    Movement,
    ParentPicker,
    ParentSelection
  }

  alias TaskmanWeb.Tasks.{Detail, Form, Recovery, Table}
  alias TaskmanWeb.ProjectSelector
  alias TaskmanWeb.WorkspaceNavigation

  @workspace_events Workspace.events()
  @listing_events Listing.events()
  @creation_events Creation.events()
  @editing_events Editing.events()
  @comment_events Comments.events()
  @parent_selection_events ParentSelection.events()
  @movement_events Movement.events()
  @recovery_events RecoveryWorkflow.events()
  @destination_unavailable_with_guidance Messages.destination_unavailable_with_guidance()
  @task_workflow_events @creation_events ++
                          @editing_events ++
                          @parent_selection_events ++ @movement_events

  @impl true
  def mount(_params, _session, socket) do
    projects = Projects.list_projects()

    socket =
      socket
      |> assign_new(:current_scope, fn -> nil end)
      |> assign_new(:current_user, fn -> nil end)

    socket =
      stream_configure(socket, :tasks,
        dom_id: fn %TaskWithLocation{task: task} -> "tasks-#{task.id}" end
      )

    socket =
      stream_configure(socket, :comments, dom_id: fn comment -> "task-comment-#{comment.id}" end)

    socket =
      stream_configure(socket, :navigation_nodes,
        dom_id: fn %Taskman.Lists.NavigationNode{dom_id: dom_id} -> dom_id end
      )

    socket =
      socket
      |> assign(:workspace, Workspace.State.new())
      |> assign(:projects_empty?, projects == [])
      |> assign(:listing, ListingState.new(TaskTablePreferences.defaults().statuses))
      |> assign(:preferences_hydrated?, false)
      |> assign(:remembered_project_restore_attempted?, false)
      |> assign(:route_key, nil)
      |> assign(:route_filter_params, %{})
      |> assign(:creation, CreationState.empty())
      |> assign(:task_parent_picker, ParentPicker.empty())
      |> assign(:editing, Editing.State.empty())
      |> assign(:comments, Comments.State.empty())
      |> assign(:comment_departure, CommentDeparture.empty())
      |> assign(:task_move, Move.empty())
      |> assign(:recovery, RecoveryWorkflow.State.empty())
      |> stream(:projects, projects)
      |> stream(:tasks, [])
      |> stream(:comments, [])
      |> stream(:navigation_nodes, [])
      |> Workspace.refresh()

    {:ok, Workspace.subscribe(socket)}
  end

  @impl true
  def handle_params(params, uri, socket) do
    destination = route_key(uri)
    departure = socket.assigns.comment_departure

    cond do
      RecoveryWorkflow.State.active?(socket.assigns.recovery) ->
        {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

      departure.confirming? && destination != departure.origin ->
        {:noreply, push_patch(socket, to: departure.origin, replace: true)}

      true ->
        case CommentDeparture.request(
               departure,
               selected_task(socket),
               destination,
               socket.assigns.comments.draft,
               socket.assigns.route_key
             ) do
          {:confirm, departure} ->
            {:noreply,
             socket
             |> assign(:comment_departure, departure)
             |> push_patch(to: departure.origin, replace: true)}

          {:pending, _departure} ->
            {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

          {:continue, departure} ->
            socket = assign(socket, :comment_departure, departure)

            case Editing.flush(socket) do
              {:ok, socket} ->
                {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

              {:error, socket} ->
                {:noreply, Editing.restore_failed_route(socket, params)}
            end
        end
    end
  end

  defp selected_task(%{
         assigns: %{
           workspace: %{selected_project: %Taskman.Projects.Project{} = project},
           editing: %{selected_task: %Task{id: id}}
         }
       }),
       do: {project, id}

  defp selected_task(_socket), do: nil

  defp assign_route_metadata(socket, params, uri) do
    socket
    |> assign(:route_key, route_key(uri))
    |> assign(:route_filter_params, TaskTablePreferences.route_params(params, uri))
  end

  defp apply_route(params, %{assigns: %{live_action: :index}} = socket) do
    socket =
      socket
      |> assign(:remembered_project_restore_attempted?, false)
      |> apply_route_preferences()

    socket =
      socket
      |> clear_task_modal_state()
      |> Workspace.assign_location(
        nil,
        nil,
        false,
        false,
        socket.assigns.workspace.include_children?,
        []
      )
      |> Listing.clear()

    if RecoveryWorkflow.State.active?(socket.assigns.recovery),
      do: RecoveryWorkflow.complete_route(socket, params),
      else: socket
  end

  defp apply_route(params, %{assigns: %{live_action: action}} = socket)
       when action in [:show, :new_task, :show_task] do
    socket = apply_route_preferences(socket)
    include_children? = socket.assigns.workspace.include_children?

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
      Comments.clear(socket)
    else
      socket
      |> ParentSelection.open_edit(project, task)
      |> Comments.load(project, task)
    end
  end

  defp apply_route_preferences(socket) do
    preferences =
      TaskTablePreferences.apply_route(
        TaskTablePreferences.current(socket),
        socket.assigns.route_filter_params
      )

    TaskTablePreferences.assign_preferences(socket, preferences, refresh?: false)
  end

  defp route_key(nil), do: nil

  defp route_key(uri) do
    parsed = URI.parse(uri)
    parsed.path <> if(parsed.query, do: "?" <> parsed.query, else: "")
  end

  @impl true
  def handle_event("hydrate_task_table_preferences", params, socket) do
    if socket.assigns.preferences_hydrated? do
      {:noreply, socket}
    else
      preferences =
        if Map.get(params, "route_key") in [nil, socket.assigns.route_key] do
          socket
          |> TaskTablePreferences.current()
          |> TaskTablePreferences.apply_hydration(params)
          |> TaskTablePreferences.apply_route(socket.assigns.route_filter_params)
        else
          TaskTablePreferences.current(socket)
        end

      {:noreply,
       socket
       |> TaskTablePreferences.assign_preferences(preferences)
       |> assign(:preferences_hydrated?, true)}
    end
  end

  def handle_event("restore_remembered_project", params, socket) do
    if socket.assigns.live_action == :index and socket.assigns.preferences_hydrated? and
         not socket.assigns.remembered_project_restore_attempted? and
         not RecoveryWorkflow.State.active?(socket.assigns.recovery) do
      socket = assign(socket, :remembered_project_restore_attempted?, true)

      case Workspace.remembered_project(Map.get(params, "project_id")) do
        {:ok, project} ->
          {:reply, %{status: "accepted"},
           push_patch(socket, to: ~p"/projects/#{project.id}", replace: true)}

        :stale ->
          {:reply, %{status: "stale"}, socket}
      end
    else
      {:reply, %{status: "ignored"}, socket}
    end
  end

  def handle_event(
        "apply_task_table_route_snapshot",
        %{"route_key" => route_key} = params,
        socket
      )
      when route_key == socket.assigns.route_key do
    preferences =
      socket
      |> TaskTablePreferences.current()
      |> TaskTablePreferences.apply_hydration(params)

    {:noreply, TaskTablePreferences.assign_preferences(socket, preferences)}
  end

  def handle_event("apply_task_table_route_snapshot", _params, socket), do: {:noreply, socket}

  def handle_event("toggle_include_children", _params, socket) do
    include_children? = not socket.assigns.workspace.include_children?
    workspace = %{socket.assigns.workspace | include_children?: include_children?}

    listing = ListingState.available_sort(socket.assigns.listing, include_children?)

    {:noreply,
     socket
     |> assign(:workspace, workspace)
     |> assign(:listing, listing)
     |> Listing.refresh()
     |> TaskTablePreferences.changed()}
  end

  def handle_event("request_task_departure", %{"destination" => destination} = params, socket) do
    draft = Map.get(params, "draft", socket.assigns.comments.draft)
    socket = if is_binary(draft), do: Comments.set_draft(socket, draft), else: socket

    case CommentDeparture.request(
           socket.assigns.comment_departure,
           selected_task(socket),
           destination,
           draft,
           socket.assigns.route_key
         ) do
      {:confirm, departure} ->
        {:noreply, assign(socket, :comment_departure, departure)}

      {:continue, departure} ->
        socket = assign(socket, :comment_departure, departure)

        case Editing.flush(socket) do
          {:ok, socket} -> {:noreply, navigate_departure(socket, destination)}
          {:error, socket} -> {:noreply, socket}
        end

      {:pending, _departure} ->
        {:noreply, socket}
    end
  end

  def handle_event("cancel_task_comment_departure", _params, socket) do
    {:noreply,
     assign(socket, :comment_departure, CommentDeparture.cancel(socket.assigns.comment_departure))}
  end

  def handle_event("discard_task_comment_departure", _params, socket) do
    case socket.assigns.comment_departure.destination do
      nil ->
        {:noreply, socket}

      destination ->
        case Editing.flush(socket, allow_invalid?: false) do
          {:ok, socket} ->
            {:noreply,
             socket
             |> Comments.discard_draft()
             |> assign(:comment_departure, CommentDeparture.empty())
             |> navigate_departure(destination)}

          {:error, socket} ->
            {:noreply,
             assign(socket, :comment_departure, %{
               socket.assigns.comment_departure
               | error: "Save the Task fields before leaving."
             })}
        end
    end
  end

  def handle_event("submit_task_comment_departure", _params, socket) do
    case socket.assigns.comment_departure do
      %CommentDeparture{destination: nil} ->
        {:noreply, socket}

      %CommentDeparture{submitting?: true} ->
        {:noreply, socket}

      %CommentDeparture{destination: destination} = departure ->
        socket = assign(socket, :comment_departure, %{departure | submitting?: true})

        case Editing.flush(socket, allow_invalid?: false) do
          {:ok, socket} ->
            case Comments.post(socket, socket.assigns.comments.draft) do
              {:ok, socket} ->
                {:noreply,
                 socket
                 |> assign(:comment_departure, CommentDeparture.empty())
                 |> navigate_departure(destination)}

              {:error, socket} ->
                {:noreply, departure_post_failed(socket, departure)}
            end

          {:error, socket} ->
            {:noreply,
             assign(socket, :comment_departure, %{
               departure
               | error: "Save the Task fields before leaving."
             })}
        end
    end
  end

  @impl true
  def handle_event(event, _params, socket)
      when event in ~w(toggle_project_selector close_project_selector toggle_mobile_sidebar close_mobile_sidebar open_project_new open_project_edit cancel_project_edit select_project_color validate_project save_project) and
             not is_nil(socket.assigns.recovery.snapshot),
      do: {:noreply, socket}

  def handle_event(event, params, socket) when event in @workspace_events,
    do: Workspace.handle_event(event, params, socket)

  def handle_event(event, params, socket) when event in @listing_events,
    do: Listing.handle_event(event, params, socket)

  def handle_event(event, params, socket) when event in @recovery_events,
    do: RecoveryWorkflow.handle_event(event, params, socket)

  def handle_event(event, _params, %{assigns: %{recovery: %{snapshot: snapshot}}} = socket)
      when event in @task_workflow_events and not is_nil(snapshot),
      do: {:noreply, socket}

  def handle_event("post_task_comment", %{"comment" => %{"text" => text}}, socket) do
    case Comments.post(socket, text) do
      {:ok, socket} ->
        {:noreply,
         assign(
           socket,
           :comment_departure,
           CommentDeparture.clear_retained(socket.assigns.comment_departure)
         )}

      {:error, socket} ->
        {:noreply, socket}
    end
  end

  def handle_event(event, params, socket) when event in @comment_events do
    Comments.handle_event(event, params, socket)
  end

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

  defp departure_post_failed(socket, departure) do
    socket
    |> assign(:comment_departure, CommentDeparture.post_failed(departure))
    |> push_event("focus_task_comment_error", %{})
  end

  defp navigate_departure(socket, destination) do
    socket = push_event(socket, "comment_departure_committed", %{})

    if String.starts_with?(destination, "/projects") or destination == "/" do
      push_patch(socket, to: destination)
    else
      redirect(socket, to: destination)
    end
  end

  defp clear_task_modal_state(socket) do
    socket
    |> clear_transient_task_modal_state()
    |> Editing.clear()
    |> Comments.clear()
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
