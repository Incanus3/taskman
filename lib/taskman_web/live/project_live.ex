defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Projects
  alias Taskman.Lists.TaskList
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.ListingRow

  alias TaskmanWeb.ProjectLive.Locations
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
    Description,
    CommentDeparture,
    Comments,
    Messages,
    Move,
    Movement,
    ParentPicker,
    ParentSelection,
    LocationScope
  }

  alias TaskmanWeb.Tasks.{Detail, Form, Recovery, Table}
  alias TaskmanWeb.ProjectSelector
  alias TaskmanWeb.WorkspaceNavigation
  alias TaskmanWeb.TaskCountBadge

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
        dom_id: fn %ListingRow{task_with_location: %{task: task}} -> "tasks-#{task.id}" end
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
      |> assign(:description, %Description.State{})
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
    state = socket.assigns.description

    cond do
      state.route_ready == destination ->
        socket = assign(socket, :description, %{state | route_ready: nil})
        continue_params(params, uri, socket)

      not is_nil(state.pending_capture) and match?({:edit, _, _}, state.context) and
          destination == socket.assigns.route_key ->
        {:noreply, assign(socket, :live_action, :show_task)}

      destination == socket.assigns.route_key or is_nil(state.editor_id) or
        match?({:new, _}, state.context) or
          RecoveryWorkflow.State.active?(socket.assigns.recovery) ->
        continue_params(params, uri, socket)

      true ->
        case Description.capture(socket, {:route, params, uri}) do
          {:ready, socket} -> continue_params(params, uri, socket)
          {:pending, socket} -> {:noreply, assign(socket, :live_action, :show_task)}
        end
    end
  end

  defp continue_params(params, uri, socket) do
    destination = route_key(uri)
    departure = socket.assigns.comment_departure

    cond do
      RecoveryWorkflow.State.active?(socket.assigns.recovery) ->
        {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

      departure.confirming? && destination != departure.origin ->
        {:noreply,
         socket |> retain_comment_detail() |> push_patch(to: departure.origin, replace: true)}

      departure.confirming? ->
        {:noreply, retain_comment_detail(socket)}

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
             |> retain_comment_detail()
             |> push_patch(to: departure.origin, replace: true)}

          {:pending, _departure} ->
            {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

          {:continue, departure} ->
            socket = assign(socket, :comment_departure, departure)

            case Editing.flush(socket, discard_done?: true) do
              {:ok, socket} ->
                {:noreply, apply_route(params, assign_route_metadata(socket, params, uri))}

              {:error, socket} ->
                {:noreply, Editing.restore_failed_route(socket, params)}
            end
        end
    end
  end

  defp retain_comment_detail(socket) do
    socket
    |> assign(:live_action, :show_task)
    |> Editing.restart_captured_saves()
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
        socket =
          socket
          |> clear_modal_state_for_action(action, params)
          |> Workspace.assign_location(
            project,
            task_list,
            false,
            false,
            include_children?,
            []
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
     |> Listing.refresh(close_hidden_move?: true)
     |> TaskTablePreferences.changed()}
  end

  def handle_event(
        "capture_task_departure_comment",
        %{"editor_id" => id, "ref" => ref, "draft" => draft},
        socket
      ) do
    handle_info({:description_comment_captured, %{editor_id: id, ref: ref, draft: draft}}, socket)
  end

  def handle_event("capture_task_departure_comment", _params, socket), do: {:noreply, socket}

  def handle_event("retry_description_capture", _params, socket) do
    {:pending, socket} = Description.retry(socket)
    {:noreply, socket}
  end

  def handle_event(
        "request_task_departure",
        _params,
        %{assigns: %{comment_departure: %CommentDeparture{confirming?: true}}} = socket
      ),
      do: {:noreply, socket}

  def handle_event(event, params, socket)
      when event in [
             "request_task_departure",
             "discard_task_comment_departure",
             "submit_task_comment_departure",
             "save_task"
           ] do
    if event == "save_task" and creation_workflow_blocked?(socket) do
      {:noreply, socket}
    else
      {socket, params} =
        if event == "request_task_departure" do
          {draft, params} = Map.pop(params, "draft")
          socket = if is_binary(draft), do: Comments.set_draft(socket, draft), else: socket
          {socket, params}
        else
          {socket, params}
        end

      purpose =
        if event == "save_task", do: {:submit, params}, else: {:departure, {event, params}}

      case Description.capture(socket, purpose) do
        {:ready, socket} -> continue_event(event, params, socket)
        {:pending, socket} -> {:noreply, socket}
      end
    end
  end

  def handle_event("cancel_task_comment_departure", _params, socket) do
    {:noreply,
     assign(socket, :comment_departure, CommentDeparture.cancel(socket.assigns.comment_departure))}
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
      case socket |> Listing.refresh_table() |> Movement.reconcile() do
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
  def handle_info({:description_capture_timeout, ref} = message, socket) do
    {:noreply, socket} = Description.handle_info(message, socket)

    case socket.assigns.description.pending_capture do
      %{ref: ^ref, timed_out?: true, purpose: {:route, params, uri}} = pending ->
        parsed = URI.parse(uri)

        destination =
          URI.to_string(%URI{path: parsed.path, query: parsed.query, fragment: parsed.fragment})

        purpose = {:departure, {"request_task_departure", %{"destination" => destination}}}

        description = %{
          socket.assigns.description
          | pending_capture: %{pending | purpose: purpose}
        }

        {:noreply,
         socket
         |> assign(:description, description)
         |> Editing.restore_failed_route(params)}

      _ ->
        {:noreply, socket}
    end
  end

  def handle_info(message, socket) do
    case Reconciliation.handle_info(message, socket) do
      {:captured, socket, purpose, markdown, dirty?} ->
        continue_capture(socket, purpose, markdown, dirty?)

      result ->
        result
    end
  end

  defp continue_event("request_task_departure", %{"destination" => destination}, socket) do
    draft = socket.assigns.comments.draft

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

        case Editing.flush(socket, discard_done?: true) do
          {:ok, socket} -> {:noreply, navigate_departure(socket, destination)}
          {:error, socket} -> {:noreply, socket}
        end

      {:pending, _departure} ->
        {:noreply, socket}
    end
  end

  defp continue_event("discard_task_comment_departure", _params, socket) do
    case socket.assigns.comment_departure.destination do
      nil ->
        {:noreply, socket}

      destination ->
        case Editing.flush(socket, allow_invalid?: false, discard_done?: true) do
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

  defp continue_event("submit_task_comment_departure", _params, socket) do
    case socket.assigns.comment_departure do
      %CommentDeparture{destination: nil} ->
        {:noreply, socket}

      %CommentDeparture{submitting?: true} ->
        {:noreply, socket}

      %CommentDeparture{destination: destination} = departure ->
        socket = assign(socket, :comment_departure, %{departure | submitting?: true})

        case Editing.flush(socket, allow_invalid?: false, discard_done?: true) do
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

  defp continue_event("save_task", params, socket),
    do: Creation.handle_event("save_task", params, socket)

  defp continue_capture(
         %{assigns: %{description: %{refresh_event: event}}} = socket,
         purpose,
         markdown,
         dirty?
       )
       when not is_nil(event) do
    context = socket.assigns.description.context
    socket = assign(socket, :description, %{socket.assigns.description | refresh_event: nil})
    {:noreply, socket} = Reconciliation.continue_captured(event, socket)

    if socket.assigns.description.context != context or not is_nil(socket.redirected) or
         RecoveryWorkflow.State.active?(socket.assigns.recovery) do
      {:noreply, socket}
    else
      continue_capture(socket, purpose, markdown, dirty?)
    end
  end

  defp continue_capture(socket, {:submit, params}, markdown, dirty?) do
    markdown = if dirty?, do: markdown, else: socket.assigns.description.initial_value

    params =
      Map.update(
        params,
        "task",
        %{"description" => markdown},
        &Map.put(&1, "description", markdown)
      )

    continue_event("save_task", params, socket)
  end

  defp continue_capture(socket, {:departure, {event, params}}, _markdown, _dirty?) do
    {:noreply, socket} = continue_event(event, params, socket)

    socket =
      if is_nil(socket.redirected), do: Editing.restart_captured_saves(socket), else: socket

    {:noreply, socket}
  end

  defp continue_capture(socket, {:route, params, uri}, _markdown, _dirty?) do
    action =
      cond do
        params["task_id"] -> :show_task
        String.ends_with?(URI.parse(uri).path, "/tasks/new") -> :new_task
        params["project_id"] -> :show
        true -> :index
      end

    continue_params(params, uri, assign(socket, :live_action, action))
  end

  defp continue_capture(socket, {:reconcile, event}, _markdown, _dirty?) do
    {:noreply, socket} = Reconciliation.continue_captured(event, socket)
    {:noreply, Editing.restart_captured_saves(socket)}
  end

  defp departure_post_failed(socket, departure) do
    socket
    |> assign(:comment_departure, CommentDeparture.post_failed(departure))
    |> push_event("focus_task_comment_error", %{})
  end

  defp navigate_departure(socket, destination) do
    socket =
      socket
      |> assign(:description, %{socket.assigns.description | route_ready: destination})
      |> push_event("comment_departure_committed", %{})

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

  defp related_task_path_fn(workspace) do
    task_lists = Locations.lists(workspace.locations, workspace.selected_project.id)
    fn summary -> related_task_path(workspace, task_lists, summary) end
  end

  defp related_task_path(workspace, task_lists, summary) do
    project = %Taskman.Projects.Project{id: summary.project_id}
    actual = if summary.location.list_id, do: %TaskList{id: summary.location.list_id}

    selected =
      if workspace.selected_project.id == project.id do
        LocationScope.backdrop(workspace, actual, task_lists)
      else
        nil
      end

    Paths.task_detail_path(project, selected, %Task{id: summary.id}, false)
  end

  defp non_creation_workflow_blocked?(socket) do
    RecoveryWorkflow.blocked?(socket) or socket.assigns.workspace.location_not_found? or
      ordinary_creation_active?(socket)
  end

  defp creation_workflow_blocked?(socket) do
    RecoveryWorkflow.blocked?(socket) or
      (socket.assigns.workspace.location_not_found? and not ordinary_creation_active?(socket))
  end

  defp form_status_options(workspace, form, list_id, revealed?) do
    TaskmanWeb.ProjectLive.Tasks.StateOptions.for_form(
      workspace.locations,
      workspace.selected_project.id,
      list_id,
      form[:status].value,
      revealed?
    )
  end

  defp creation_list_id("list:" <> id) do
    case Integer.parse(id) do
      {value, ""} when value > 0 -> value
      _ -> nil
    end
  end

  defp creation_list_id(_), do: nil
end
