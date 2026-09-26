defmodule TaskmanWeb.ProjectLive.Reconciliation do
  @moduledoc "Coordinates validated external notifications across workspace workflows."

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Recovery
  alias TaskmanWeb.ProjectLive.Tasks.{Creation, Editing, Listing, Movement, ParentSelection}

  @doc "Handles scheduled autosaves and validated workspace notifications."
  @spec handle_info(term(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_info({:autosave_task_field, _task_id, _field, _revision} = message, socket),
    do:
      if(Recovery.blocked?(socket) or socket.assigns.workspace.location_not_found?,
        do: {:noreply, socket},
        else: Editing.handle_autosave_info(message, socket)
      )

  def handle_info(%Event{entity: entity} = event, socket) when entity in [:project, :list] do
    socket =
      if well_formed_workspace_event?(event) do
        previous_workspace = socket.assigns.workspace

        case Workspace.reconcile(socket, event) do
          {socket, :unchanged} ->
            socket

          {socket, {:location_missing, task_lists}} ->
            socket = Recovery.capture(socket, previous_workspace)

            case Movement.reconcile(socket) do
              {:anchored, socket} ->
                reconcile_missing_location(socket, previous_workspace, task_lists)

              {:missing, socket} = movement_result ->
                recover_detail_or_apply_movement(socket, previous_workspace, movement_result)

              movement_result ->
                Movement.apply_reconciliation(movement_result)
            end

          {socket, {:location_changed, task_lists}} ->
            if Recovery.State.active?(socket.assigns.recovery) do
              Listing.refresh(socket)
            else
              socket
              |> Creation.refresh_locations(task_lists)
              |> Listing.refresh()
              |> reconcile_movement()
              |> ParentSelection.refresh()
              |> Editing.reload_hierarchy()
            end
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
    socket = Workspace.refresh(socket)

    if Recovery.State.active?(socket.assigns.recovery) do
      Listing.refresh(socket)
    else
      socket = Listing.refresh(socket)

      case Movement.reconcile(socket) do
        {:anchored, socket} ->
          socket =
            socket
            |> Editing.reconcile(event)
            |> sync_parent_selection()

          if task_hierarchy_affected?(event) do
            Editing.reload_hierarchy(socket)
          else
            socket
          end

        movement_result ->
          Movement.apply_reconciliation(movement_result)
      end
    end
  end

  defp reconcile_movement(socket) do
    socket
    |> Movement.reconcile()
    |> Movement.apply_reconciliation()
  end

  defp reconcile_missing_location(socket, previous_workspace, task_lists) do
    if socket.assigns.live_action == :new_task and socket.assigns.creation.form do
      socket
      |> Creation.refresh_locations(task_lists)
      |> Listing.clear()
    else
      Recovery.enter(socket, previous_workspace)
    end
  end

  defp recover_detail_or_apply_movement(socket, previous_workspace, movement_result) do
    if socket.assigns.live_action == :new_task and socket.assigns.creation.form do
      Movement.apply_reconciliation(movement_result)
    else
      recovered = Recovery.enter(socket, previous_workspace)

      if Recovery.State.active?(recovered.assigns.recovery) do
        recovered
      else
        Movement.apply_reconciliation(movement_result)
      end
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
         operation: operation,
         project_id: project_id,
         entity_id: entity_id,
         lock_version: nil,
         fields: fields
       })
       when operation in [:created, :updated] and is_integer(project_id) and project_id > 0 and
              entity_id == project_id and
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
