defmodule TaskmanWeb.ProjectLive.Reconciliation do
  @moduledoc "Coordinates validated external notifications across workspace workflows."

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Tasks.{Creation, Editing, Listing, Movement, ParentSelection}

  @doc "Handles scheduled autosaves and validated workspace notifications."
  @spec handle_info(term(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_info({:autosave_task_field, _task_id, _field, _revision} = message, socket),
    do: Editing.handle_autosave_info(message, socket)

  def handle_info(%Event{entity: entity} = event, socket) when entity in [:project, :list] do
    socket =
      if well_formed_workspace_event?(event) do
        case Workspace.reconcile(socket, event) do
          {socket, :unchanged} ->
            socket

          {socket, {:location_missing, task_lists}} ->
            socket
            |> Creation.refresh_location(task_lists)
            |> Listing.clear()

          {socket, {:location_changed, task_lists}} ->
            socket
            |> Creation.refresh_location(task_lists)
            |> Listing.refresh()
            |> Movement.reconcile()
            |> ParentSelection.refresh()
            |> Editing.reload_hierarchy()
        end
      else
        socket
      end

    {:noreply, socket}
  end

  def handle_info(
        %Event{entity: :task} = event,
        %{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket
      ) do
    socket =
      if well_formed_task_event?(event) and event.project_id == project.id do
        reconcile_task_event(socket, event)
      else
        socket
      end

    {:noreply, socket}
  end

  def handle_info(%Event{entity: :task}, socket), do: {:noreply, socket}
  def handle_info(%Event{}, socket), do: {:noreply, socket}

  defp reconcile_task_event(socket, event) do
    socket =
      socket
      |> Listing.refresh()
      |> Movement.reconcile()
      |> Editing.reconcile(event)
      |> sync_parent_selection()

    if task_hierarchy_affected?(event) do
      Editing.reload_hierarchy(socket)
    else
      socket
    end
  end

  defp sync_parent_selection({socket, {:task_reconciled, persisted_task}}) do
    ParentSelection.sync(socket, persisted_task)
  end

  defp sync_parent_selection({socket, :unchanged}), do: socket

  defp well_formed_task_event?(%Event{
         operation: operation,
         project_id: project_id,
         entity_id: entity_id,
         lock_version: lock_version,
         fields: fields
       })
       when operation in [:created, :updated, :moved] and is_integer(project_id) and
              project_id > 0 and
              is_integer(entity_id) and entity_id > 0 and
              (is_nil(lock_version) or (is_integer(lock_version) and lock_version >= 0)) and
              is_list(fields) do
    Enum.all?(fields, &is_atom/1)
  end

  defp well_formed_task_event?(_event), do: false

  defp well_formed_workspace_event?(%Event{
         entity: :project,
         operation: :created,
         project_id: project_id,
         entity_id: entity_id,
         lock_version: nil,
         fields: fields
       })
       when is_integer(project_id) and project_id > 0 and entity_id == project_id and
              is_list(fields) do
    Enum.all?(fields, &is_atom/1)
  end

  defp well_formed_workspace_event?(%Event{
         entity: :list,
         operation: operation,
         project_id: project_id,
         entity_id: entity_id,
         lock_version: nil,
         fields: fields
       })
       when operation in [:created, :updated] and is_integer(project_id) and project_id > 0 and
              is_integer(entity_id) and entity_id > 0 and is_list(fields) do
    Enum.all?(fields, &is_atom/1)
  end

  defp well_formed_workspace_event?(_event), do: false

  defp task_hierarchy_affected?(%Event{operation: operation, fields: fields}) do
    operation in [:created, :moved] or
      Enum.any?(fields, &(&1 in [:title, :status, :parent_task_id, :list_id]))
  end
end
