defmodule TaskmanWeb.ProjectLive.Tasks.Activity do
  @moduledoc false

  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [stream: 4]

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  defmodule State do
    @moduledoc false
    defstruct task_id: nil, empty?: true, revision: 0
    def empty, do: %__MODULE__{}
  end

  def clear(socket) do
    socket
    |> assign(:activity, State.empty())
    |> stream(:activity, [], reset: true)
  end

  def load(socket, project, task) do
    if socket.assigns.activity.task_id == task.id,
      do: socket,
      else: reconcile(socket, project, task)
  end

  def reconcile(socket, project, task) do
    case Tasks.list_activity(project, task, include_comments: true) do
      {:ok, entries} ->
        state = socket.assigns.activity

        socket
        |> assign(:activity, %State{
          task_id: task.id,
          empty?: entries == [],
          revision: state.revision + 1
        })
        |> stream(:activity, entries, reset: true)

      {:error, :not_found} ->
        clear(socket)
    end
  end

  def refresh_selected(
        %{
          assigns: %{
            workspace: %{selected_project: project},
            editing: %{selected_task: %Task{} = task}
          }
        } = socket
      ),
      do: reconcile(socket, project, task)

  def refresh_selected(socket), do: clear(socket)
end
