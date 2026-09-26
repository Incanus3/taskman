defmodule TaskmanWeb.ProjectLive.Tasks.Movement do
  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [push_patch: 2, put_flash: 3, stream_insert: 3]

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Workspace

  alias TaskmanWeb.ProjectLive.Tasks.{
    Editing,
    Listing,
    LocationScope,
    Messages,
    Move,
    ParentSelection
  }

  @events ~w(
    open_move_task
    open_move_destinations
    close_move_destinations
    search_move_destinations
    select_move_destination
    cancel_move_task
    submit_move_task
  )

  @spec events() :: [String.t()]
  def events, do: @events

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("open_move_task", %{"task-id" => task_id}, socket) do
    case socket.assigns.workspace.selected_project do
      %Project{} = project ->
        case Tasks.get_task_for_project(project, task_id) do
          %Task{} = task ->
            task_move =
              Move.open(
                socket.assigns.task_move,
                project,
                task,
                task_move_origin(socket, task)
              )

            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> Listing.refresh()}

          nil ->
            {:noreply, assign(socket, :task_move, Move.clear(socket.assigns.task_move))}
        end

      nil ->
        {:noreply, assign(socket, :task_move, Move.clear(socket.assigns.task_move))}
    end
  end

  def handle_event("open_move_task", _params, socket),
    do: {:noreply, assign(socket, :task_move, Move.clear(socket.assigns.task_move))}

  def handle_event("open_move_destinations", _params, socket) do
    {:noreply,
     socket
     |> assign(:task_move, Move.open_destinations(socket.assigns.task_move))
     |> refresh()}
  end

  def handle_event("close_move_destinations", _params, socket) do
    {:noreply,
     socket
     |> assign(:task_move, Move.close_destinations(socket.assigns.task_move))
     |> reinsert_active_move_row()}
  end

  def handle_event("search_move_destinations", %{"value" => query}, socket)
      when is_binary(query) do
    case {socket.assigns.workspace.selected_project, socket.assigns.task_move} do
      {%Project{} = project, %Move{} = task_move} ->
        if Move.active?(task_move) do
          case Move.search(task_move, project, query) do
            {:ok, task_move, _task} ->
              {:noreply,
               socket
               |> assign(:task_move, task_move)
               |> Listing.refresh()}

            {:error, task_move, :task_not_found} ->
              {:noreply,
               socket
               |> assign(:task_move, task_move)
               |> Listing.refresh()}
          end
        else
          {:noreply, socket}
        end

      _no_active_move ->
        {:noreply, socket}
    end
  end

  def handle_event("search_move_destinations", _params, socket), do: {:noreply, socket}

  def handle_event("select_move_destination", %{"destination" => destination}, socket)
      when is_binary(destination) do
    {:noreply,
     socket
     |> assign(:task_move, Move.select_destination(socket.assigns.task_move, destination))
     |> refresh()}
  end

  def handle_event("select_move_destination", _params, socket), do: {:noreply, socket}

  def handle_event("cancel_move_task", _params, socket) do
    {:noreply,
     socket
     |> assign(:task_move, Move.clear(socket.assigns.task_move))
     |> Listing.refresh()}
  end

  def handle_event(
        "submit_move_task",
        _params,
        %{
          assigns: %{
            task_move: %Move{active_task: %{origin: origin}},
            workspace: %{selected_project: %Project{} = project}
          }
        } =
          socket
      ) do
    case flush_move_task_fields(origin, socket) do
      {:error, socket} ->
        task_move =
          Move.put_error(
            socket.assigns.task_move,
            "Save the Task before moving it."
          )

        {:noreply,
         socket
         |> assign(:task_move, task_move)
         |> reinsert_active_move_row()}

      {:ok, socket} ->
        case Move.submit(socket.assigns.task_move, project) do
          {:ok, task_move, moved_task} ->
            socket =
              socket
              |> assign(:task_move, task_move)
              |> Editing.refresh_after_move(moved_task.id)
              |> Editing.reload_hierarchy()
              |> Workspace.refresh()
              |> Listing.refresh()

            {:noreply, navigate_after_move(socket, project, moved_task, origin)}

          {:error, task_move, _reason} ->
            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> reinsert_active_move_row()}
        end
    end
  end

  def handle_event("submit_move_task", _params, socket), do: {:noreply, socket}

  @doc "Refreshes the move destination surface and visible Task rows. Returns the updated socket."
  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            task_move: %Move{} = task_move
          }
        } = socket
      ) do
    if Move.active?(task_move) do
      case Move.refresh(task_move, project) do
        {:ok, task_move, _task} ->
          socket
          |> assign(:task_move, task_move)
          |> Listing.refresh()

        {:error, task_move, :task_not_found} ->
          socket
          |> assign(:task_move, task_move)
          |> Listing.refresh()
      end
    else
      Listing.refresh(socket)
    end
  end

  def refresh(socket), do: socket

  @doc "Reconciles active movement against fresh Task, listing, and destination authority."
  @spec reconcile(Phoenix.LiveView.Socket.t()) ::
          {:anchored, Phoenix.LiveView.Socket.t()}
          | {:relocate, Phoenix.LiveView.Socket.t(), Task.t()}
          | {:missing, Phoenix.LiveView.Socket.t()}
  def reconcile(
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            task_move: %Move{} = task_move
          }
        } = socket
      ) do
    if Move.active?(task_move) do
      case Move.refresh(task_move, project) do
        {:ok, refreshed_move, task} ->
          changed? = refreshed_move != task_move
          socket = assign(socket, :task_move, refreshed_move)

          if row_anchor_lost?(socket, refreshed_move, task) do
            case restore_move(refreshed_move, project, task, :detail) do
              {:ok, task_move} -> {:relocate, assign(socket, :task_move, task_move), task}
              {:error, :task_not_found} -> {:missing, assign(socket, :task_move, Move.empty())}
            end
          else
            socket = if changed?, do: reinsert_active_move_row(socket), else: socket
            {:anchored, socket}
          end

        {:error, task_move, :task_not_found} ->
          {:missing, assign(socket, :task_move, task_move)}
      end
    else
      {:anchored, socket}
    end
  end

  def reconcile(socket), do: {:anchored, socket}

  @doc "Applies a reconciliation outcome without submitting movement."
  @spec apply_reconciliation(
          {:anchored, Phoenix.LiveView.Socket.t()}
          | {:relocate, Phoenix.LiveView.Socket.t(), Task.t()}
          | {:missing, Phoenix.LiveView.Socket.t()}
        ) :: Phoenix.LiveView.Socket.t()
  def apply_reconciliation({:anchored, socket}), do: socket

  def apply_reconciliation(
        {:relocate, %{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket,
         %Task{} = task}
      ) do
    destination = actual_location(project, task)

    socket
    |> Editing.apply_route(project, task)
    |> ParentSelection.open_edit(project, task)
    |> push_patch(
      to:
        Paths.task_detail_path(
          project,
          destination,
          task,
          socket.assigns.workspace.include_children?
        )
    )
  end

  def apply_reconciliation({:missing, socket}) do
    socket =
      socket
      |> Listing.refresh()
      |> put_flash(:error, Messages.task_unavailable())

    if socket.assigns.workspace.location_not_found? do
      project = socket.assigns.workspace.selected_project

      push_patch(socket,
        to: Paths.browse_path(project, nil, socket.assigns.workspace.include_children?)
      )
    else
      socket
    end
  end

  @doc "Rebuilds a captured move against fresh authority without submitting it."
  @spec restore_move(Move.t(), Project.t(), Task.t(), Move.origin()) ::
          {:ok, Move.t()} | {:error, :task_not_found}
  def restore_move(%Move{} = captured, %Project{} = project, %Task{} = task, origin) do
    rebuilt = Move.open(Move.empty(), project, task, origin)

    canonical_destination = Enum.find(rebuilt.options, &(&1.key == captured.destination))

    case Move.search(rebuilt, project, captured.query) do
      {:ok, searched, _task} ->
        restored =
          cond do
            canonical_destination ->
              rebuilt
              |> Move.select_destination(canonical_destination.key)
              |> Map.put(:options_open?, captured.options_open?)

            is_binary(captured.destination) and is_nil(canonical_destination) ->
              %{
                searched
                | destination: nil,
                  query: captured.query,
                  options_open?: captured.options_open?,
                  error: Messages.destination_unavailable_with_guidance()
              }

            true ->
              %{
                searched
                | query: captured.query,
                  options_open?: captured.options_open?,
                  error: captured.error
              }
          end

        {:ok, restored}

      {:error, _cleared, :task_not_found} ->
        {:error, :task_not_found}
    end
  end

  @doc "Clears movement state for a modal transition. Returns the updated socket."
  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :task_move, Move.clear(socket.assigns.task_move))

  defp row_anchor_lost?(
         socket,
         %Move{active_task: %{origin: :row}},
         %Task{id: task_id}
       ) do
    workspace = socket.assigns.workspace

    workspace.location_not_found? or
      not Enum.any?(
        Listing.list_tasks_for_location(
          workspace.selected_project,
          workspace.selected_list,
          workspace.include_children?,
          socket.assigns.listing.visible_statuses,
          socket.assigns.listing.sort
        ),
        &(&1.task.id == task_id)
      )
  end

  defp row_anchor_lost?(_socket, %Move{}, %Task{}), do: false

  defp navigate_after_move(socket, project, moved_task, origin) do
    workspace = socket.assigns.workspace
    task_lists = Lists.list_lists_for_project(project)
    actual = actual_location(project, moved_task)
    backdrop = LocationScope.backdrop(workspace, actual, task_lists)

    if backdrop == workspace.selected_list and not workspace.location_not_found? do
      socket
    else
      path =
        case origin do
          :detail ->
            Paths.task_detail_path(project, backdrop, moved_task, workspace.include_children?)

          :row ->
            Paths.browse_path(project, backdrop, workspace.include_children?)
        end

      push_patch(socket, to: path)
    end
  end

  defp actual_location(_project, %Task{list_id: nil}), do: nil

  defp actual_location(project, %Task{list_id: list_id}) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> task_list
      nil -> nil
    end
  end

  defp task_move_origin(socket, %Task{id: task_id}) do
    case socket.assigns.editing.selected_task do
      %Task{id: ^task_id} -> :detail
      _not_selected -> :row
    end
  end

  defp flush_move_task_fields(:detail, socket) do
    case Editing.flush(socket) do
      {:ok, %{assigns: %{editing: %{autosave: %{form: %{source: %{valid?: true}}}}}} = socket} ->
        {:ok, socket}

      {:ok, socket} ->
        {:error, socket}

      {:error, socket} ->
        {:error, socket}
    end
  end

  defp flush_move_task_fields(_origin, socket), do: {:ok, socket}

  defp reinsert_active_move_row(
         %{
           assigns: %{
             task_move: %Move{
               active_task: %{origin: :row, task_with_location: %TaskWithLocation{} = task}
             }
           }
         } = socket
       ) do
    stream_insert(socket, :tasks, task)
  end

  defp reinsert_active_move_row(socket), do: socket
end
