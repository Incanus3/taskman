defmodule TaskmanWeb.ProjectLive.Tasks.Movement do
  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [stream_insert: 3]

  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.ProjectLive.Tasks.{Editing, Listing, Move}

  @events ~w(
    open_move_task
    open_move_destinations
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
            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> Editing.refresh_after_move(moved_task.id)
             |> Listing.refresh()}

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

  @doc "Reconciles active movement without opening destinations or resetting the Task stream."
  @spec reconcile(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
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
        {:ok, task_move, _task} -> assign(socket, :task_move, task_move)
        {:error, task_move, :task_not_found} -> assign(socket, :task_move, task_move)
      end
    else
      socket
    end
  end

  def reconcile(socket), do: socket

  @doc "Clears movement state for a modal transition. Returns the updated socket."
  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :task_move, Move.clear(socket.assigns.task_move))

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
