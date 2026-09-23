defmodule TaskmanWeb.ProjectLive.Recovery do
  @moduledoc "Owns captured Task input, fresh continuation, and exceptional recovery."

  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [push_patch: 2]

  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths

  alias TaskmanWeb.ProjectLive.Tasks.{
    Creation,
    Editing,
    Listing,
    LocationScope,
    Messages,
    Move,
    Movement,
    ParentSelection
  }

  @events ~w(resume_task_recovery discard_task_recovery)

  defmodule State do
    @moduledoc false

    defstruct snapshot: nil, sequence: 0, pending: nil, error: nil, reason: nil

    @type t :: %__MODULE__{
            snapshot: map() | nil,
            sequence: non_neg_integer(),
            pending: map() | nil,
            error: String.t() | nil,
            reason: atom() | nil
          }

    @spec empty() :: t()
    def empty, do: %__MODULE__{}

    @spec active?(t()) :: boolean()
    def active?(%__MODULE__{snapshot: snapshot}), do: not is_nil(snapshot)

    @spec capture(t(), map() | nil) :: t()
    def capture(%__MODULE__{snapshot: snapshot} = state, _capture) when not is_nil(snapshot),
      do: state

    def capture(%__MODULE__{} = state, capture) when is_map(capture) do
      sequence = state.sequence + 1

      %{
        state
        | sequence: sequence,
          snapshot: Map.put(capture, :id, sequence),
          error: nil,
          reason: nil
      }
    end

    def capture(%__MODULE__{} = state, nil), do: state

    @spec matches?(t(), term()) :: boolean()
    def matches?(%__MODULE__{snapshot: %{id: id}}, recovery_id) when is_binary(recovery_id),
      do: recovery_id == Integer.to_string(id)

    def matches?(%__MODULE__{}, _recovery_id), do: false

    @spec prepare(t(), map()) :: t()
    def prepare(%__MODULE__{} = state, pending) when is_map(pending),
      do: %{state | pending: pending, error: nil, reason: nil}

    @spec put_error(t(), String.t(), atom() | nil) :: t()
    def put_error(%__MODULE__{} = state, error, reason \\ nil) when is_binary(error),
      do: %{state | pending: nil, error: error, reason: reason}

    @spec discard(t()) :: t()
    def discard(%__MODULE__{} = state) do
      %{state | snapshot: nil, pending: nil, error: nil, reason: nil}
    end
  end

  @spec events() :: [String.t()]
  def events, do: @events

  @spec enter(Phoenix.LiveView.Socket.t(), TaskmanWeb.ProjectLive.Workspace.State.t()) ::
          Phoenix.LiveView.Socket.t()
  def enter(socket, previous_workspace) do
    socket = capture(socket, previous_workspace)
    recovery = socket.assigns.recovery

    if State.active?(recovery) do
      socket
      |> Creation.clear()
      |> ParentSelection.clear()
      |> Movement.clear()
      |> Editing.clear()
      |> Listing.clear()
      |> continue_detail()
    else
      Listing.clear(socket)
    end
  end

  @doc "Captures recoverable detail state before another workflow refreshes its authority."
  @spec capture(Phoenix.LiveView.Socket.t(), TaskmanWeb.ProjectLive.Workspace.State.t()) ::
          Phoenix.LiveView.Socket.t()
  def capture(socket, previous_workspace) do
    recovery =
      State.capture(socket.assigns.recovery, capture_snapshot(socket, previous_workspace))

    assign(socket, :recovery, recovery)
  end

  @spec blocked?(Phoenix.LiveView.Socket.t()) :: boolean()
  def blocked?(socket) do
    State.active?(socket.assigns.recovery) or
      is_nil(socket.assigns.workspace.selected_project) or
      socket.assigns.workspace.project_not_found?
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("discard_task_recovery", %{"recovery_id" => recovery_id}, socket) do
    if State.matches?(socket.assigns.recovery, recovery_id) do
      {:noreply, discard(socket)}
    else
      {:noreply, socket}
    end
  end

  def handle_event("resume_task_recovery", %{"recovery_id" => recovery_id}, socket) do
    if State.matches?(socket.assigns.recovery, recovery_id) do
      resume_detail(socket)
    else
      {:noreply, socket}
    end
  end

  def handle_event(event, _params, socket) when event in @events, do: {:noreply, socket}

  @spec complete_route(Phoenix.LiveView.Socket.t(), map()) :: Phoenix.LiveView.Socket.t()
  def complete_route(socket, params) do
    recovery = socket.assigns.recovery

    cond do
      not State.active?(recovery) ->
        socket

      is_nil(recovery.pending) ->
        canonicalize_task_action(socket)

      not pending_path_matches?(socket, params, recovery.pending) ->
        continue_detail(socket)

      true ->
        complete_pending_route(socket, params, recovery.pending)
    end
  end

  @spec view(Phoenix.LiveView.Socket.t() | State.t()) :: map() | nil
  def view(%{assigns: %{recovery: %State{} = recovery}}), do: view(recovery)

  def view(%State{snapshot: nil}), do: nil
  def view(%State{pending: pending}) when not is_nil(pending), do: nil

  def view(%State{snapshot: snapshot, error: error, reason: reason}) do
    %{
      id: snapshot.id,
      editing: Map.get(snapshot, :editing),
      parent_picker: snapshot.parent_picker,
      task_move: Map.get(snapshot, :task_move),
      copy_value: copy_value(snapshot),
      error: error,
      reason: reason
    }
  end

  defp capture_snapshot(
         %{assigns: %{live_action: :show_task, editing: %{selected_task: %{id: _}}}} = socket,
         %{selected_project: %Project{} = project, selected_list: task_list} = workspace
       ) do
    %{
      source_project: project,
      disappeared_list: task_list,
      include_children?: workspace.include_children?,
      editing: socket.assigns.editing,
      parent_picker: socket.assigns.task_parent_picker,
      task_move: socket.assigns.task_move
    }
  end

  defp capture_snapshot(_socket, _workspace), do: nil

  defp discard(socket) do
    path = discard_path(socket)

    socket
    |> assign(:recovery, State.discard(socket.assigns.recovery))
    |> Creation.clear()
    |> ParentSelection.clear()
    |> Movement.clear()
    |> Editing.clear()
    |> push_patch(to: path)
  end

  defp resume_detail(%{assigns: %{recovery: %{snapshot: _snapshot}}} = socket) do
    {:noreply, continue_detail(socket)}
  end

  defp continue_detail(socket) do
    recovery = socket.assigns.recovery
    snapshot = recovery.snapshot

    case resolve_continuation(snapshot) do
      {:ok, project, task, backdrop} ->
        continue_to_detail(socket, project, task, backdrop)

      {:error, :project_not_found} ->
        put_recovery_error(socket, project_unavailable_error(), :project_not_found)

      {:error, :task_not_found} ->
        put_recovery_error(socket, task_unavailable_error(), :task_not_found)

      {:error, :destination_not_found} ->
        put_recovery_error(socket, destination_unavailable_error(), :destination_not_found)
    end
  end

  defp continue_to_detail(socket, project, task, backdrop) do
    snapshot = socket.assigns.recovery.snapshot

    if current_detail_route_matches?(socket, project, task, backdrop) do
      restore_detail(socket, project, task)
    else
      pending = pending(snapshot, project, task, backdrop)

      socket
      |> assign(:recovery, State.prepare(socket.assigns.recovery, pending))
      |> push_patch(
        to: Paths.task_detail_path(project, backdrop, task, snapshot.include_children?),
        replace: true
      )
    end
  end

  defp pending_path_matches?(socket, params, pending),
    do: pending_detail_path_matches?(socket, params, pending)

  defp pending_detail_path_matches?(socket, params, pending) do
    pending.snapshot_id == socket.assigns.recovery.snapshot.id and
      socket.assigns.live_action == :show_task and
      Map.get(params, "project_id") == Integer.to_string(pending.project_id) and
      Map.get(params, "task_id") == Integer.to_string(pending.task_id) and
      route_param_list_id(params) == pending.list_id and
      Map.get(params, "include_children") == if(pending.include_children?, do: "true", else: nil)
  end

  defp complete_pending_route(socket, params, pending) do
    snapshot = socket.assigns.recovery.snapshot

    case resolve_continuation(snapshot) do
      {:ok, project, task, backdrop} ->
        desired = pending(snapshot, project, task, backdrop)

        cond do
          desired != pending ->
            socket
            |> assign(:recovery, State.prepare(socket.assigns.recovery, desired))
            |> push_patch(
              to:
                Paths.task_detail_path(
                  project,
                  backdrop,
                  task,
                  snapshot.include_children?
                ),
              replace: true
            )

          matching_detail_route?(socket, params, desired) ->
            restore_detail(socket, project, task)

          true ->
            put_recovery_error(socket, destination_unavailable_error(), :destination_not_found)
        end

      {:error, :project_not_found} ->
        put_recovery_error(socket, project_unavailable_error(), :project_not_found)

      {:error, :task_not_found} ->
        put_recovery_error(socket, task_unavailable_error(), :task_not_found)

      {:error, :destination_not_found} ->
        put_recovery_error(socket, destination_unavailable_error(), :destination_not_found)
    end
  end

  defp matching_detail_route?(socket, params, pending) do
    workspace = socket.assigns.workspace

    pending_detail_path_matches?(socket, params, pending) and valid_backdrop?(socket) and
      workspace.selected_project.id == pending.project_id and
      route_list_id(workspace.selected_list) == pending.list_id
  end

  defp restore_detail(socket, project, task) do
    recovery = socket.assigns.recovery
    snapshot = recovery.snapshot

    with {:ok, socket} <- Editing.restore(socket, snapshot.editing, snapshot.parent_picker),
         {:ok, socket} <- restore_detail_move(socket, snapshot.task_move, project, task) do
      socket
      |> assign(:recovery, State.discard(recovery))
      |> Listing.refresh()
    else
      {:error, :task_not_found, socket} ->
        put_recovery_error(socket, task_unavailable_error(), :task_not_found)

      {:error, socket} ->
        put_recovery_error(
          socket,
          "Couldn’t restore your input. Try again or copy it.",
          :restore_failed
        )
    end
  end

  defp restore_detail_move(socket, %Move{} = captured_move, project, task) do
    if Move.active?(captured_move) do
      case Movement.restore_move(captured_move, project, task, :detail) do
        {:ok, task_move} -> {:ok, assign(socket, :task_move, task_move)}
        {:error, :task_not_found} -> {:error, :task_not_found, socket}
      end
    else
      {:ok, socket}
    end
  end

  defp resolve_task_location(_project, %Task{list_id: nil}), do: {:ok, nil}

  defp resolve_task_location(project, %Task{list_id: list_id}) do
    case Lists.get_list_for_project(project, list_id) do
      nil -> {:error, :destination_not_found}
      task_list -> {:ok, task_list}
    end
  end

  defp resolve_detail_target(project_id, task_id) do
    case Projects.get_project(project_id) do
      nil ->
        {:error, :project_not_found}

      %Project{} = project ->
        case Tasks.get_task_for_project(project, task_id) do
          nil ->
            {:error, :task_not_found}

          %Task{} = task ->
            case resolve_task_location(project, task) do
              {:ok, destination} -> {:ok, project, task, destination}
              {:error, :destination_not_found} = error -> error
            end
        end
    end
  end

  defp resolve_continuation(snapshot) do
    task_id = snapshot.editing.selected_task.id

    with {:ok, project, task, actual} <-
           resolve_detail_target(snapshot.source_project.id, task_id) do
      task_lists = Lists.list_lists_for_project(project)
      backdrop = LocationScope.backdrop(snapshot_workspace(snapshot), actual, task_lists)
      {:ok, project, task, backdrop}
    end
  end

  defp snapshot_workspace(snapshot) do
    %{
      selected_list: snapshot.disappeared_list,
      include_children?: snapshot.include_children?,
      location_not_found?: false
    }
  end

  defp pending(snapshot, project, task, backdrop) do
    %{
      snapshot_id: snapshot.id,
      project_id: project.id,
      task_id: task.id,
      list_id: route_list_id(backdrop),
      include_children?: snapshot.include_children?
    }
  end

  defp current_detail_route_matches?(socket, project, task, backdrop) do
    workspace = socket.assigns.workspace

    socket.assigns.live_action == :show_task and valid_backdrop?(socket) and
      workspace.selected_project.id == project.id and
      route_list_id(workspace.selected_list) == route_list_id(backdrop) and
      snapshot_task_id(socket.assigns.recovery.snapshot) == task.id
  end

  defp snapshot_task_id(snapshot), do: snapshot.editing.selected_task.id

  defp route_list_id(nil), do: nil
  defp route_list_id(%{id: id}), do: id

  defp route_param_list_id(%{"list_id" => id}) do
    case Integer.parse(id) do
      {value, ""} when value > 0 ->
        if Integer.to_string(value) == id, do: value, else: :invalid

      _ ->
        :invalid
    end
  end

  defp route_param_list_id(_params), do: nil

  defp canonicalize_task_action(socket) do
    if socket.assigns.live_action in [:new_task, :show_task] and valid_backdrop?(socket) do
      push_patch(socket, to: current_browse_path(socket), replace: true)
    else
      socket
    end
  end

  defp put_recovery_error(socket, error, reason) do
    assign(socket, :recovery, State.put_error(socket.assigns.recovery, error, reason))
  end

  defp project_unavailable_error,
    do: "This Project is no longer available. Copy your input or discard it."

  defp destination_unavailable_error, do: Messages.destination_unavailable()

  defp task_unavailable_error,
    do: "This Task is no longer available. Copy your unsaved input or discard it."

  defp discard_path(socket) do
    if valid_backdrop?(socket) do
      current_browse_path(socket)
    else
      snapshot = socket.assigns.recovery.snapshot
      Paths.browse_path(snapshot.source_project, nil, snapshot.include_children?)
    end
  end

  defp valid_backdrop?(socket) do
    not is_nil(socket.assigns.workspace.selected_project) and
      not socket.assigns.workspace.project_not_found? and
      not socket.assigns.workspace.location_not_found?
  end

  defp current_browse_path(socket) do
    workspace = socket.assigns.workspace

    Paths.browse_path(
      workspace.selected_project,
      workspace.selected_list,
      workspace.include_children?
    )
  end

  defp copy_value(%{editing: %{autosave: %{form: form}}, parent_picker: picker}),
    do: field_copy(form, picker)

  defp field_copy(form, picker) do
    parent = if picker.selected_parent, do: picker.selected_parent.title, else: "No parent"

    [
      {"Task title", form[:title].value},
      {"Description", form[:description].value},
      {"Status", form[:status].value},
      {"Priority", form[:priority].value},
      {"Due date and time", form[:due_at].value},
      {"Parent Task", parent}
    ]
    |> Enum.map_join("\n", fn {label, value} -> "#{label}: #{format_value(value)}" end)
  end

  defp format_value(nil), do: ""
  defp format_value(%NaiveDateTime{} = value), do: Calendar.strftime(value, "%Y-%m-%dT%H:%M")
  defp format_value(value), do: to_string(value)
end
