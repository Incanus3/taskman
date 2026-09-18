defmodule TaskmanWeb.ProjectLive.Tasks.ParentSelection do
  import Phoenix.Component, only: [assign: 3]

  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Tasks.{Editing, Listing, ParentPicker}

  @events ~w(
    open_task_parent_options
    toggle_task_parent_options
    close_task_parent_options
    search_task_parents
    task_parent_keydown
    select_task_parent
    clear_task_parent
    resolve_task_parent_conflict
  )

  @spec events() :: [String.t()]
  def events, do: @events

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("open_task_parent_options", _params, socket) do
    {:noreply,
     update_picker(
       socket,
       false,
       &ParentPicker.open_options(&1, socket.assigns.workspace.selected_project)
     )}
  end

  def handle_event("toggle_task_parent_options", _params, socket) do
    {:noreply,
     update_picker(
       socket,
       false,
       &ParentPicker.toggle_options(&1, socket.assigns.workspace.selected_project)
     )}
  end

  def handle_event("close_task_parent_options", _params, socket) do
    {:noreply, update_picker(socket, false, &ParentPicker.close_options/1)}
  end

  def handle_event("search_task_parents", %{"parent_query" => query}, socket)
      when is_binary(query) do
    picker = socket.assigns.task_parent_picker

    if not picker.options_open? and query == picker.query do
      {:noreply, socket}
    else
      {:noreply,
       update_picker(
         socket,
         false,
         &ParentPicker.search(&1, socket.assigns.workspace.selected_project, query)
       )}
    end
  end

  def handle_event("search_task_parents", _params, socket), do: {:noreply, socket}

  def handle_event("task_parent_keydown", %{"key" => key}, socket) when is_binary(key) do
    picker = socket.assigns.task_parent_picker

    case ParentPicker.keydown(picker, key) do
      {:move, picker} ->
        {:noreply, assign(socket, :task_parent_picker, picker)}

      {:close, picker} ->
        {:noreply, assign(socket, :task_parent_picker, picker)}

      {:select, parent_id} ->
        {:noreply,
         update_picker(
           socket,
           true,
           &ParentPicker.select_draft(&1, socket.assigns.workspace.selected_project, parent_id)
         )}

      :ignore ->
        {:noreply, socket}
    end
  end

  def handle_event("select_task_parent", %{"parent-id" => parent_id}, socket) do
    {:noreply,
     update_picker(
       socket,
       true,
       &ParentPicker.select_draft(&1, socket.assigns.workspace.selected_project, parent_id)
     )}
  end

  def handle_event("clear_task_parent", _params, socket) do
    {:noreply, update_picker(socket, true, &ParentPicker.clear_draft/1)}
  end

  def handle_event(
        "resolve_task_parent_conflict",
        %{"field" => "parent_task_id", "resolution" => resolution},
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            task_parent_picker: %ParentPicker{} = picker
          }
        } = socket
      )
      when is_binary(resolution) do
    case conflict_resolution(resolution) do
      nil ->
        {:noreply, socket}

      resolution ->
        case ParentPicker.resolve_conflict(picker, project, resolution) do
          {:ok, picker, task} -> {:noreply, sync_persisted_picker(socket, picker, task)}
          {:conflict, picker, task} -> {:noreply, sync_persisted_picker(socket, picker, task)}
          {:error, picker, _reason} -> {:noreply, assign(socket, :task_parent_picker, picker)}
        end
    end
  end

  def handle_event("resolve_task_parent_conflict", _params, socket), do: {:noreply, socket}

  @doc "Initializes the picker for an open Task detail route."
  @spec open_edit(Phoenix.LiveView.Socket.t(), Project.t(), Task.t()) ::
          Phoenix.LiveView.Socket.t()
  def open_edit(socket, %Project{} = project, %Task{} = task) do
    assign(
      socket,
      :task_parent_picker,
      ParentPicker.open_edit(ParentPicker.empty(), project, task)
    )
  end

  @doc "Reconciles the open detail picker with the resulting persisted selected Task."
  @spec sync(Phoenix.LiveView.Socket.t(), Task.t() | nil) :: Phoenix.LiveView.Socket.t()
  def sync(
        %{
          assigns: %{
            live_action: :show_task,
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{id: task_id}},
            task_parent_picker: %ParentPicker{} = picker
          }
        } = socket,
        %Task{id: task_id} = task
      ) do
    assign(socket, :task_parent_picker, ParentPicker.reconcile(picker, project, task))
  end

  def sync(socket, _task), do: socket

  @doc "Refreshes open parent candidates after the workspace location changes."
  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(
        %{
          assigns: %{
            task_parent_picker: %ParentPicker{options_open?: true} = picker,
            workspace: %{selected_project: %Project{} = project}
          }
        } = socket
      ) do
    assign(socket, :task_parent_picker, ParentPicker.search(picker, project, picker.query))
  end

  def refresh(socket), do: socket

  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :task_parent_picker, ParentPicker.empty())

  defp update_picker(
         %{assigns: %{live_action: :new_task, task_parent_picker: %ParentPicker{}}} = socket,
         _save?,
         transition
       ),
       do: assign(socket, :task_parent_picker, transition.(socket.assigns.task_parent_picker))

  defp update_picker(
         %{
           assigns: %{
             live_action: :show_task,
             workspace: %{selected_project: %Project{} = project},
             editing: %{selected_task: %Task{} = task},
             task_parent_picker: %ParentPicker{} = picker
           }
         } = socket,
         save?,
         transition
       ) do
    picker = transition.(picker)

    if save? and not picker.parent_conflicted? do
      save_picker(socket, project, task, picker)
    else
      assign(socket, :task_parent_picker, picker)
    end
  end

  defp update_picker(socket, _save?, _transition), do: socket

  defp save_picker(socket, project, task, picker) do
    case ParentPicker.save_edit(picker, project, task) do
      {:ok, picker, updated_task} ->
        socket
        |> sync_persisted_picker(picker, updated_task)
        |> Editing.reload_hierarchy()
        |> Listing.refresh()

      {:conflict, picker, current_task} ->
        socket
        |> sync_persisted_picker(picker, current_task)
        |> Editing.reload_hierarchy()
        |> Listing.refresh()

      {:error, picker, _reason} ->
        assign(socket, :task_parent_picker, picker)
    end
  end

  defp sync_persisted_picker(socket, %ParentPicker{} = picker, %Task{} = task) do
    socket
    |> Editing.sync_persisted_task(task)
    |> assign(:task_parent_picker, picker)
  end

  defp conflict_resolution("use_latest"), do: :use_latest
  defp conflict_resolution("keep_mine"), do: :keep_mine
  defp conflict_resolution(_resolution), do: nil
end
