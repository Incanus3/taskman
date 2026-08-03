defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.{TaskComponents, TaskForm}

  @editable_task_fields ~w(title description status priority due_at)
  @debounced_task_fields ~w(title description)

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(:selected_project, nil)
     |> assign(:project_not_found?, false)
     |> assign(:project_form, project_form(%Project{}))
     |> assign(:task_form, nil)
     |> assign(:task_create_enabled?, false)
     |> assign(:tasks_empty?, true)
     |> assign(:selected_task, nil)
     |> assign(:task_not_found?, false)
     |> assign(:task_draft, %{})
     |> assign(:task_dirty_fields, MapSet.new())
     |> assign(:task_revisions, %{})
     |> assign(:task_autosave_sequence, 0)
     |> assign(:task_save_failed?, false)
     |> assign(:task_saved?, false)
     |> assign(:task_save_state, :idle)
     |> stream(:projects, Projects.list_projects())
     |> stream(:tasks, [])}
  end

  @impl true
  def handle_params(params, _uri, socket) do
    case flush_dirty_task_fields(socket) do
      {:ok, socket} -> {:noreply, apply_route(params, socket)}
      {:error, socket} -> {:noreply, restore_failed_task_route(socket, params)}
    end
  end

  defp apply_route(_params, %{assigns: %{live_action: :index}} = socket) do
    socket
    |> clear_task_modal_state()
    |> assign_project_state(nil, false, [])
  end

  defp apply_route(
         %{"project_id" => project_id},
         %{assigns: %{live_action: :new_task}} = socket
       ) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        changeset = Tasks.change_task(project)

        socket
        |> clear_task_modal_state()
        |> assign_project_state(project, false, Tasks.list_tasks_for_project(project))
        |> assign(:task_form, to_form(changeset))
        |> assign(:task_create_enabled?, changeset.valid?)

      nil ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(nil, true, [])
    end
  end

  defp apply_route(
         %{"project_id" => project_id, "task_id" => task_id},
         %{assigns: %{live_action: :show_task}} = socket
       ) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        socket =
          socket
          |> clear_task_modal_state()
          |> assign_project_state(project, false, Tasks.list_tasks_for_project(project))

        case Tasks.get_task_for_project(project, task_id) do
          %Task{} = task ->
            socket
            |> assign(:selected_task, task)
            |> assign(:task_form, to_form(Tasks.change_task(task)))

          nil ->
            assign(socket, :task_not_found?, true)
        end

      nil ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(nil, true, [])
    end
  end

  defp apply_route(%{"project_id" => project_id}, socket) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(project, false, Tasks.list_tasks_for_project(project))

      nil ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(nil, true, [])
    end
  end

  @impl true
  def handle_event("validate_project", %{"project" => project_params}, socket) do
    form =
      %Project{}
      |> Projects.change_project(project_params)
      |> Map.put(:action, :validate)
      |> to_form()

    {:noreply, assign(socket, :project_form, form)}
  end

  def handle_event("save_project", %{"project" => project_params}, socket) do
    case Projects.create_project(project_params) do
      {:ok, project} ->
        {:noreply,
         socket
         |> assign(:project_form, project_form(%Project{}))
         |> push_patch(to: ~p"/projects/#{project.id}")}

      {:error, changeset} ->
        {:noreply, assign(socket, :project_form, to_form(changeset))}
    end
  end

  def handle_event("validate_task", %{"task" => task_params}, socket) do
    changeset =
      socket.assigns.selected_project
      |> Tasks.change_task(task_params)
      |> Map.put(:action, :validate)

    {:noreply,
     socket
     |> assign(:task_form, to_form(changeset))
     |> assign(:task_create_enabled?, changeset.valid?)}
  end

  def handle_event("save_task", %{"task" => task_params}, socket) do
    case Tasks.create_task(socket.assigns.selected_project, task_params) do
      {:ok, task} ->
        {:noreply,
         socket
         |> assign(:tasks_empty?, false)
         |> stream_insert(:tasks, task)
         |> push_patch(to: ~p"/projects/#{socket.assigns.selected_project.id}")}

      {:error, changeset} ->
        {:noreply,
         socket
         |> assign(:task_form, to_form(changeset))
         |> assign(:task_create_enabled?, false)}
    end
  end

  def handle_event(
        "autosave_task",
        %{"_target" => ["task", field], "task" => task_params},
        socket
      )
      when field in @editable_task_fields do
    socket =
      socket
      |> assign(:task_draft, task_params)
      |> assign(:task_save_failed?, false)
      |> assign_task_form(task_params)
      |> mark_task_field_dirty(field)

    socket =
      if field in @debounced_task_fields do
        schedule_task_field_save(socket, field)
      else
        persist_task_field(socket, field)
      end

    {:noreply, socket}
  end

  def handle_event("submit_task_edit", _params, socket) do
    case flush_dirty_task_fields(socket) do
      {:ok, socket} -> {:noreply, socket}
      {:error, socket} -> {:noreply, socket}
    end
  end

  @impl true
  def handle_info(
        {:autosave_task_field, task_id, field, revision},
        %{assigns: %{selected_task: %{id: task_id}, task_revisions: revisions}} = socket
      ) do
    if Map.get(revisions, field) == revision do
      {:noreply, persist_task_field(socket, field)}
    else
      {:noreply, socket}
    end
  end

  def handle_info({:autosave_task_field, _task_id, _field, _revision}, socket) do
    {:noreply, socket}
  end

  defp assign_project_state(socket, selected_project, project_not_found?, tasks) do
    socket
    |> assign(:selected_project, selected_project)
    |> assign(:project_not_found?, project_not_found?)
    |> assign(:tasks_empty?, tasks == [])
    |> stream(:projects, Projects.list_projects(), reset: true)
    |> stream(:tasks, tasks, reset: true)
  end

  defp clear_task_modal_state(socket) do
    socket
    |> assign(:selected_task, nil)
    |> assign(:task_not_found?, false)
    |> assign(:task_form, nil)
    |> assign(:task_create_enabled?, false)
    |> assign(:task_draft, %{})
    |> assign(:task_dirty_fields, MapSet.new())
    |> assign(:task_revisions, %{})
    |> assign(:task_save_failed?, false)
    |> assign(:task_saved?, false)
    |> assign(:task_save_state, :idle)
  end

  defp assign_task_form(socket, params) do
    form =
      socket.assigns.selected_task
      |> Tasks.change_task(params)
      |> Map.put(:action, :validate)
      |> to_form()

    assign(socket, :task_form, form)
  end

  defp mark_task_field_dirty(socket, field) do
    assign(
      socket,
      :task_dirty_fields,
      MapSet.put(socket.assigns.task_dirty_fields, field)
    )
  end

  defp schedule_task_field_save(socket, field) do
    revision = socket.assigns.task_autosave_sequence + 1

    socket =
      socket
      |> assign(:task_autosave_sequence, revision)
      |> assign(:task_revisions, Map.put(socket.assigns.task_revisions, field, revision))

    value = Map.get(socket.assigns.task_draft, field)
    field_changeset = Tasks.change_task(socket.assigns.selected_task, %{field => value})

    if field_changeset.valid? do
      message = {:autosave_task_field, socket.assigns.selected_task.id, field, revision}
      schedule_autosave_message(message)
    end

    refresh_task_save_state(socket)
  end

  defp schedule_autosave_message(message) do
    case Application.get_env(:taskman, :task_autosave_delay_ms, 300) do
      0 -> send(self(), message)
      delay -> Process.send_after(self(), message, delay)
    end
  end

  defp flush_dirty_task_fields(%{assigns: %{selected_task: %Task{}}} = socket) do
    {socket, save_failed?} =
      Enum.reduce(socket.assigns.task_dirty_fields, {socket, false}, fn field,
                                                                        {socket, save_failed?} ->
        socket =
          socket
          |> assign(:task_save_failed?, false)
          |> persist_task_field(field)

        {socket, save_failed? || socket.assigns.task_save_failed?}
      end)

    socket =
      socket
      |> assign(:task_save_failed?, save_failed?)
      |> refresh_task_save_state()

    if save_failed?, do: {:error, socket}, else: {:ok, socket}
  end

  defp flush_dirty_task_fields(socket), do: {:ok, socket}

  defp restore_failed_task_route(
         %{assigns: %{selected_project: %Project{} = project, selected_task: %Task{} = task}} =
           socket,
         params
       ) do
    if selected_task_route?(params, project, task) do
      socket
    else
      push_patch(socket,
        to: ~p"/projects/#{project.id}/tasks/#{task.id}",
        replace: true
      )
    end
  end

  defp selected_task_route?(
         %{"project_id" => project_id, "task_id" => task_id},
         %Project{id: selected_project_id},
         %Task{id: selected_task_id}
       ) do
    project_id == Integer.to_string(selected_project_id) &&
      task_id == Integer.to_string(selected_task_id)
  end

  defp selected_task_route?(_params, _project, _task), do: false

  defp persist_task_field(socket, field) do
    value = Map.get(socket.assigns.task_draft, field)
    field_changeset = Tasks.change_task(socket.assigns.selected_task, %{field => value})

    if field_changeset.valid? do
      save_task_field(socket, field, value)
    else
      refresh_task_save_state(socket)
    end
  end

  defp save_task_field(socket, field, value) do
    case Tasks.update_task(
           socket.assigns.selected_project,
           socket.assigns.selected_task,
           %{field => value}
         ) do
      {:ok, task} ->
        socket
        |> assign(:selected_task, task)
        |> assign(:task_dirty_fields, MapSet.delete(socket.assigns.task_dirty_fields, field))
        |> assign(:task_save_failed?, false)
        |> assign(:task_saved?, true)
        |> stream_insert(:tasks, task)
        |> assign_task_form(socket.assigns.task_draft)
        |> refresh_task_save_state()

      {:error, %Ecto.Changeset{}} ->
        socket
        |> assign(:task_save_failed?, true)
        |> refresh_task_save_state()

      {:error, :not_found} ->
        socket
        |> assign(:selected_task, nil)
        |> assign(:task_form, nil)
        |> assign(:task_not_found?, true)
    end
  end

  defp refresh_task_save_state(socket) do
    task_save_state =
      cond do
        socket.assigns.task_save_failed? -> :failed
        !socket.assigns.task_form.source.valid? -> :not_saved
        MapSet.size(socket.assigns.task_dirty_fields) > 0 -> :saving
        socket.assigns.task_saved? -> :saved
        true -> :idle
      end

    assign(socket, :task_save_state, task_save_state)
  end

  defp task_save_message(:idle), do: "Autosaves changes"
  defp task_save_message(:saving), do: "Saving…"
  defp task_save_message(:saved), do: "Saved"
  defp task_save_message(:not_saved), do: "Not saved"
  defp task_save_message(:failed), do: "Couldn’t save changes"

  defp project_form(project), do: to_form(Projects.change_project(project))
end
