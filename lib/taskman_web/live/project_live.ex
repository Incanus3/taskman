defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  alias TaskmanWeb.{
    ListEdit,
    TaskAutosave,
    TaskComponents,
    TaskForm,
    TaskHierarchy,
    TaskMove,
    TaskParentPicker,
    WorkspaceNavigation
  }

  @impl true
  def mount(_params, _session, socket) do
    socket =
      stream_configure(socket, :tasks,
        dom_id: fn %TaskWithLocation{task: task} -> "tasks-#{task.id}" end
      )

    socket =
      stream_configure(socket, :navigation_nodes,
        dom_id: fn %Taskman.Lists.NavigationNode{dom_id: dom_id} -> dom_id end
      )

    {:ok,
     socket
     |> assign(:selected_project, nil)
     |> assign(:project_not_found?, false)
     |> assign(:selected_list, nil)
     |> assign(:include_children?, false)
     |> assign(:location_not_found?, false)
     |> assign(:location_path, [])
     |> assign(:project_form, project_form(%Project{}))
     |> assign(:task_create_form, nil)
     |> assign(:task_create_enabled?, false)
     |> assign(:task_create_location, nil)
     |> assign(:task_parent_picker, TaskParentPicker.empty())
     |> assign(:tasks_empty?, true)
     |> assign(:selected_task, nil)
     |> assign(:task_not_found?, false)
     |> assign(:task_detail_open?, false)
     |> assign(:task_autosave, TaskAutosave.empty())
     |> assign(:task_hierarchy, TaskHierarchy.empty())
     |> assign(:task_move, TaskMove.empty())
     |> assign(:expanded_node_ids, MapSet.new())
     |> assign(:list_edit, ListEdit.empty())
     |> stream(:projects, Projects.list_projects())
     |> stream(:tasks, [])
     |> stream(:navigation_nodes, [])
     |> refresh_navigation_stream()}
  end

  @impl true
  def handle_params(params, _uri, socket) do
    case flush_task_autosave(socket) do
      {:ok, socket} -> {:noreply, apply_route(params, socket)}
      {:error, socket} -> {:noreply, restore_failed_task_route(socket, params)}
    end
  end

  defp apply_route(_params, %{assigns: %{live_action: :index}} = socket) do
    socket
    |> clear_task_modal_state()
    |> assign_project_state(nil, nil, false, false, false, [], [])
  end

  defp apply_route(params, %{assigns: %{live_action: action}} = socket)
       when action in [:show, :new_task, :show_task] do
    include_children? = include_children?(params)

    case resolve_location(params) do
      {:ok, project, task_list} ->
        tasks = list_tasks_for_location(project, task_list, include_children?)
        location_path = location_path(project, task_list)

        socket =
          socket
          |> clear_modal_state_for_action(action)
          |> assign_project_state(
            project,
            task_list,
            false,
            false,
            include_children?,
            location_path,
            tasks
          )

        apply_action(action, params, socket)

      {:error, :project_not_found} ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(nil, nil, true, false, include_children?, [], [])

      {:error, :location_not_found, project} ->
        socket
        |> clear_task_modal_state()
        |> assign_project_state(
          project,
          nil,
          false,
          true,
          include_children?,
          [],
          []
        )
    end
  end

  defp resolve_location(%{"project_id" => project_id} = params) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        case Map.fetch(params, "list_id") do
          :error ->
            {:ok, project, nil}

          {:ok, list_id} ->
            case Lists.get_list_for_project(project, list_id) do
              %TaskList{} = task_list -> {:ok, project, task_list}
              nil -> {:error, :location_not_found, project}
            end
        end

      nil ->
        {:error, :project_not_found}
    end
  end

  defp resolve_location(_params), do: {:error, :project_not_found}

  defp apply_action(:show, _params, socket), do: socket

  defp apply_action(:new_task, params, socket) do
    changeset = Tasks.change_task(socket.assigns.selected_project)

    {task_create_location, task_parent_picker} =
      task_create_state(
        socket.assigns.selected_project,
        socket.assigns.selected_list,
        params
      )

    socket
    |> assign(:task_create_form, to_form(changeset))
    |> assign(:task_create_enabled?, changeset.valid?)
    |> assign(:task_create_location, task_create_location)
    |> assign(:task_parent_picker, task_parent_picker)
  end

  defp apply_action(:show_task, %{"task_id" => task_id}, socket) do
    case Tasks.get_task_for_project(socket.assigns.selected_project, task_id) do
      %Task{} = task ->
        socket
        |> assign(:selected_task, task)
        |> assign(:task_detail_open?, true)
        |> assign(
          :task_autosave,
          TaskAutosave.load(socket.assigns.task_autosave, task, saved?: false)
        )
        |> assign(
          :task_parent_picker,
          TaskParentPicker.open_edit(
            TaskParentPicker.empty(),
            socket.assigns.selected_project,
            task
          )
        )
        |> reload_task_hierarchy(socket.assigns.selected_project, task)

      nil ->
        task_not_found_modal_state(socket)
    end
  end

  defp list_tasks_for_location(project, task_list, include_children?) do
    case Tasks.list_tasks_for_location(
           project,
           task_list,
           include_descendants: include_children?
         ) do
      {:ok, tasks} -> tasks
      {:error, :not_found} -> []
    end
  end

  defp location_path(_project, nil), do: []

  defp location_path(project, %TaskList{} = task_list) do
    project
    |> Lists.list_lists_for_project()
    |> Lists.path_for(task_list)
  end

  defp include_children?(params), do: Map.get(params, "include_children") == "true"

  defp main_panel_state(assigns) do
    cond do
      assigns.project_not_found? -> "not-found"
      assigns.location_not_found? -> "location-not-found"
      assigns.selected_project -> "selected"
      true -> "no-selection"
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

  def handle_event("toggle_navigation_node", params, socket) when is_map(params) do
    case navigation_identity(params) do
      nil ->
        {:noreply, socket}

      identity ->
        expanded_node_ids =
          if MapSet.member?(socket.assigns.expanded_node_ids, identity) do
            MapSet.delete(socket.assigns.expanded_node_ids, identity)
          else
            MapSet.put(socket.assigns.expanded_node_ids, identity)
          end

        {:noreply,
         socket
         |> assign(:expanded_node_ids, expanded_node_ids)
         |> refresh_navigation_stream()}
    end
  end

  def handle_event("toggle_task_hierarchy_node", %{"task-id" => task_id}, socket) do
    case parse_navigation_identity(task_id) do
      {:ok, task_id} ->
        {:noreply,
         assign(
           socket,
           :task_hierarchy,
           TaskHierarchy.toggle(socket.assigns.task_hierarchy, task_id)
         )}

      :error ->
        {:noreply, socket}
    end
  end

  def handle_event("toggle_task_hierarchy_node", _params, socket), do: {:noreply, socket}

  def handle_event("open_list_form", params, socket) do
    case action_project(socket, params) do
      %Project{} = project ->
        case Map.get(params, "kind") do
          "new" -> open_new_list_form(socket, project, parent_id_param(params))
          "rename" -> open_rename_list_form(socket, project, list_id_param(params))
          _other -> {:noreply, socket}
        end

      _project_not_selected ->
        {:noreply, socket}
    end
  end

  def handle_event("cancel_list_form", _params, socket) do
    {:noreply,
     socket
     |> clear_list_edit()
     |> refresh_navigation_stream()}
  end

  def handle_event("validate_list", %{"list" => list_params}, socket) do
    case ListEdit.validate(socket.assigns.list_edit, list_params) do
      {:ok, list_edit} ->
        {:noreply,
         socket
         |> assign(:list_edit, list_edit)
         |> refresh_navigation_stream()}

      {:error, :not_found} ->
        {:noreply, socket}
    end
  end

  def handle_event("save_list", %{"list" => list_params}, socket) do
    case {socket.assigns.list_edit, ListEdit.target(socket.assigns.list_edit)} do
      {%ListEdit{project: %Project{} = project}, {:ok, {:new, parent}, _task_list}} ->
        case Lists.create_list(project, parent, list_params) do
          {:ok, _task_list} ->
            expanded_node_ids =
              MapSet.put(
                socket.assigns.expanded_node_ids,
                if(parent, do: {:list, parent.id}, else: {:project, project.id})
              )

            {:noreply,
             socket
             |> assign(:expanded_node_ids, expanded_node_ids)
             |> clear_list_edit()
             |> refresh_navigation_stream()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_edit_error(socket, changeset)}

          {:error, :not_found} ->
            {:noreply, socket}
        end

      {%ListEdit{project: %Project{} = project}, {:ok, {:rename, current_list}, _task_list}} ->
        case Lists.rename_list(project, current_list, list_params) do
          {:ok, renamed_list} ->
            selected_list =
              case socket.assigns.selected_list do
                %TaskList{id: id} when id == renamed_list.id -> renamed_list
                selected -> selected
              end

            selected_project = socket.assigns.selected_project

            selected_location_path =
              case {selected_project, selected_list} do
                {%Project{} = current_project, %TaskList{}} ->
                  location_path(current_project, selected_list)

                _no_selected_list ->
                  []
              end

            {:noreply,
             socket
             |> assign(:selected_list, selected_list)
             |> assign(:location_path, selected_location_path)
             |> clear_list_edit()
             |> refresh_navigation_stream()
             |> refresh_task_stream()
             |> refresh_move_surface()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_edit_error(socket, changeset)}

          {:error, :not_found} ->
            {:noreply, socket}
        end

      _invalid_form ->
        {:noreply, socket}
    end
  end

  def handle_event("validate_task", %{"task" => task_params}, socket) do
    changeset =
      socket.assigns.selected_project
      |> Tasks.change_task(task_params)
      |> Map.put(:action, :validate)

    {:noreply,
     socket
     |> assign(:task_create_form, to_form(changeset))
     |> assign(:task_create_enabled?, changeset.valid?)}
  end

  def handle_event("open_task_parent_options", _params, socket) do
    {:noreply,
     update_task_parent_picker(
       socket,
       false,
       &TaskParentPicker.open_options(&1, socket.assigns.selected_project)
     )}
  end

  def handle_event("search_task_parents", %{"parent_query" => query}, socket)
      when is_binary(query) do
    picker = socket.assigns.task_parent_picker

    if not picker.options_open? and query == picker.query do
      {:noreply, socket}
    else
      {:noreply,
       update_task_parent_picker(
         socket,
         false,
         &TaskParentPicker.search(&1, socket.assigns.selected_project, query)
       )}
    end
  end

  def handle_event("task_parent_keydown", %{"key" => key}, socket) when is_binary(key) do
    picker = socket.assigns.task_parent_picker

    case TaskParentPicker.keydown(picker, key) do
      {:move, picker} ->
        {:noreply, assign(socket, :task_parent_picker, picker)}

      {:close, picker} ->
        {:noreply, assign(socket, :task_parent_picker, picker)}

      {:select, parent_id} ->
        {:noreply,
         update_task_parent_picker(
           socket,
           true,
           &TaskParentPicker.select_draft(&1, socket.assigns.selected_project, parent_id)
         )}

      :ignore ->
        {:noreply, socket}
    end
  end

  def handle_event("select_task_parent", %{"parent-id" => parent_id}, socket) do
    {:noreply,
     update_task_parent_picker(
       socket,
       true,
       &TaskParentPicker.select_draft(&1, socket.assigns.selected_project, parent_id)
     )}
  end

  def handle_event("clear_task_parent", _params, socket) do
    {:noreply, update_task_parent_picker(socket, true, &TaskParentPicker.clear_draft/1)}
  end

  def handle_event("save_task", %{"task" => task_params}, socket) do
    case Tasks.create_task(
           socket.assigns.selected_project,
           socket.assigns.task_create_location,
           task_params,
           parent: TaskParentPicker.selected_parent(socket.assigns.task_parent_picker)
         ) do
      {:ok, _task} ->
        socket = refresh_task_stream(socket)

        {:noreply,
         push_patch(socket,
           to:
             browse_path(
               socket.assigns.selected_project,
               socket.assigns.selected_list,
               socket.assigns.include_children?
             )
         )}

      {:error, :not_found} ->
        {:noreply,
         socket
         |> assign(
           :task_parent_picker,
           TaskParentPicker.reject_draft(
             socket.assigns.task_parent_picker,
             "That parent Task is no longer available."
           )
         )}

      {:error, changeset} ->
        {:noreply,
         socket
         |> assign(:task_create_form, to_form(changeset))
         |> assign(:task_create_enabled?, false)}
    end
  end

  def handle_event(
        "autosave_task",
        %{"_target" => ["task", field], "task" => task_params},
        socket
      ) do
    result =
      TaskAutosave.change(
        socket.assigns.task_autosave,
        socket.assigns.selected_project,
        socket.assigns.selected_task,
        task_params,
        field
      )

    {:noreply, apply_task_autosave_result(socket, result)}
  end

  def handle_event("submit_task_edit", _params, socket) do
    case flush_task_autosave(socket) do
      {:ok, socket} -> {:noreply, socket}
      {:error, socket} -> {:noreply, socket}
    end
  end

  def handle_event("open_move_task", %{"task-id" => task_id}, socket) do
    case socket.assigns.selected_project do
      %Project{} = project ->
        case Tasks.get_task_for_project(project, task_id) do
          %Task{} = task ->
            task_move =
              TaskMove.open(
                socket.assigns.task_move,
                project,
                task,
                task_move_origin(socket, task)
              )

            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> refresh_task_stream()}

          nil ->
            {:noreply, assign(socket, :task_move, TaskMove.clear(socket.assigns.task_move))}
        end

      nil ->
        {:noreply, assign(socket, :task_move, TaskMove.clear(socket.assigns.task_move))}
    end
  end

  def handle_event("open_move_task", _params, socket),
    do: {:noreply, assign(socket, :task_move, TaskMove.clear(socket.assigns.task_move))}

  def handle_event("open_move_destinations", _params, socket) do
    {:noreply,
     socket
     |> assign(:task_move, TaskMove.open_destinations(socket.assigns.task_move))
     |> refresh_move_surface()}
  end

  def handle_event("search_move_destinations", %{"value" => query}, socket)
      when is_binary(query) do
    case {socket.assigns.selected_project, socket.assigns.task_move} do
      {%Project{} = project, %TaskMove{} = task_move} ->
        if TaskMove.active?(task_move) do
          case TaskMove.search(task_move, project, query) do
            {:ok, task_move, _task} ->
              {:noreply,
               socket
               |> assign(:task_move, task_move)
               |> refresh_task_stream()}

            {:error, task_move, :task_not_found} ->
              {:noreply,
               socket
               |> assign(:task_move, task_move)
               |> refresh_task_stream()}
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
     |> assign(:task_move, TaskMove.select_destination(socket.assigns.task_move, destination))
     |> refresh_move_surface()}
  end

  def handle_event("select_move_destination", _params, socket), do: {:noreply, socket}

  def handle_event("cancel_move_task", _params, socket),
    do:
      {:noreply,
       socket
       |> assign(:task_move, TaskMove.clear(socket.assigns.task_move))
       |> refresh_task_stream()}

  def handle_event(
        "submit_move_task",
        _params,
        %{
          assigns: %{
            task_move: %TaskMove{active_task: %{origin: origin}},
            selected_project: %Project{} = project
          }
        } =
          socket
      ) do
    case flush_move_task_fields(origin, socket) do
      {:error, socket} ->
        task_move =
          TaskMove.put_error(
            socket.assigns.task_move,
            "Save the Task before moving it."
          )

        {:noreply,
         socket
         |> assign(:task_move, task_move)
         |> reinsert_active_move_row()}

      {:ok, socket} ->
        case TaskMove.submit(socket.assigns.task_move, project) do
          {:ok, task_move, moved_task} ->
            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> refresh_selected_task_after_move(moved_task.id)
             |> refresh_task_stream()}

          {:error, task_move, _reason} ->
            {:noreply,
             socket
             |> assign(:task_move, task_move)
             |> reinsert_active_move_row()}
        end
    end
  end

  def handle_event("submit_move_task", _params, socket), do: {:noreply, socket}

  @impl true
  def handle_info(
        {:autosave_task_field, task_id, field, revision},
        %{
          assigns: %{
            selected_project: %Project{} = project,
            selected_task: %Task{} = task
          }
        } = socket
      ) do
    result =
      TaskAutosave.handle_scheduled_save(
        socket.assigns.task_autosave,
        project,
        task,
        task_id,
        field,
        revision
      )

    {:noreply, apply_task_autosave_result(socket, result)}
  end

  def handle_info({:autosave_task_field, _task_id, _field, _revision}, socket) do
    {:noreply, socket}
  end

  defp apply_task_autosave_result(socket, {:ok, autosave, task}),
    do: sync_task_autosave(socket, autosave, task)

  defp apply_task_autosave_result(socket, {:ignored, autosave, task}),
    do: sync_task_autosave(socket, autosave, task)

  defp apply_task_autosave_result(
         socket,
         {:schedule, autosave, task, delay_ms, message}
       ) do
    socket
    |> sync_task_autosave(autosave, task)
    |> execute_task_autosave_schedule(delay_ms, message)
  end

  defp apply_task_autosave_result(socket, {:not_found, autosave}) do
    socket
    |> assign(:task_autosave, autosave)
    |> assign(:selected_task, nil)
    |> assign(:task_not_found?, true)
  end

  defp sync_task_autosave(socket, autosave, task) do
    socket = assign(socket, :task_autosave, autosave)

    if socket.assigns.selected_task != task do
      socket
      |> assign(:selected_task, task)
      |> refresh_task_stream()
    else
      socket
    end
  end

  defp execute_task_autosave_schedule(socket, 0, message) do
    send(self(), message)
    socket
  end

  defp execute_task_autosave_schedule(socket, delay_ms, message) do
    Process.send_after(self(), message, delay_ms)
    socket
  end

  defp navigation_identity(%{"kind" => "project", "id" => id} = params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id", id))

    with {:ok, node_id} <- parse_navigation_identity(id),
         {:ok, owning_project_id} <- parse_navigation_identity(project_id),
         true <- node_id == owning_project_id,
         %Project{} <- Projects.get_project(node_id) do
      {:project, node_id}
    else
      _invalid -> nil
    end
  end

  defp navigation_identity(%{"kind" => "list", "id" => id} = params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    with {:ok, list_id} <- parse_navigation_identity(id),
         {:ok, project_id} <- parse_navigation_identity(project_id),
         %Project{} = project <- Projects.get_project(project_id),
         %TaskList{} <- Lists.get_list_for_project(project, list_id) do
      {:list, list_id}
    else
      _invalid -> nil
    end
  end

  defp navigation_identity(_params), do: nil

  defp parse_navigation_identity(id) do
    case Integer.parse(to_string(id)) do
      {parsed, ""} when parsed > 0 -> {:ok, parsed}
      _invalid -> :error
    end
  end

  defp parent_id_param(params), do: Map.get(params, "parent-id", Map.get(params, "parent_id", ""))
  defp list_id_param(params), do: Map.get(params, "list-id", Map.get(params, "list_id"))

  defp action_project(socket, params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    case project_id do
      nil -> socket.assigns.selected_project
      "" -> nil
      id -> Projects.get_project(id)
    end
  end

  defp open_new_list_form(socket, project, parent_id) do
    case resolve_parent_list(project, parent_id) do
      {:ok, parent} ->
        {:noreply,
         socket
         |> assign(:list_edit, ListEdit.open_new(project, parent))
         |> refresh_navigation_stream()}

      :error ->
        {:noreply, socket}
    end
  end

  defp open_rename_list_form(socket, project, list_id) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list ->
        {:noreply,
         socket
         |> assign(:list_edit, ListEdit.open_rename(project, task_list))
         |> refresh_navigation_stream()}

      nil ->
        {:noreply, socket}
    end
  end

  defp resolve_parent_list(_project, parent_id) when parent_id in [nil, ""], do: {:ok, nil}

  defp resolve_parent_list(project, parent_id) do
    case Lists.get_list_for_project(project, parent_id) do
      %TaskList{} = parent -> {:ok, parent}
      nil -> :error
    end
  end

  defp assign_list_edit_error(socket, changeset) do
    socket
    |> assign(:list_edit, ListEdit.put_error(socket.assigns.list_edit, changeset))
    |> refresh_navigation_stream()
  end

  defp clear_list_edit(socket) do
    assign(socket, :list_edit, ListEdit.clear(socket.assigns.list_edit))
  end

  defp refresh_navigation_stream(socket) do
    projects = Projects.list_projects()

    lists_by_project =
      Map.new(projects, fn project ->
        {project.id, Lists.list_lists_for_project(project)}
      end)

    nodes =
      Lists.navigation_nodes(
        projects,
        lists_by_project,
        selected_location(socket),
        socket.assigns.expanded_node_ids
      )

    stream(socket, :navigation_nodes, nodes, reset: true)
  end

  defp selected_location(%{
         assigns: %{selected_project: %Project{id: project_id}, selected_list: nil}
       }),
       do: {:project, project_id}

  defp selected_location(%{assigns: %{selected_list: %TaskList{id: list_id}}}),
    do: {:list, list_id}

  defp selected_location(_socket), do: nil

  defp assign_project_state(
         socket,
         selected_project,
         selected_list,
         project_not_found?,
         location_not_found?,
         include_children?,
         location_path,
         tasks
       ) do
    socket
    |> assign(:selected_project, selected_project)
    |> assign(:selected_list, selected_list)
    |> assign(:project_not_found?, project_not_found?)
    |> assign(:location_not_found?, location_not_found?)
    |> assign(:include_children?, include_children?)
    |> assign(:location_path, location_path)
    |> clear_list_edit()
    |> assign(:tasks_empty?, tasks == [])
    |> stream(:projects, Projects.list_projects(), reset: true)
    |> stream(:tasks, tasks, reset: true)
    |> refresh_navigation_stream()
  end

  defp refresh_task_stream(
         %{
           assigns: %{
             selected_project: %Project{} = project,
             selected_list: task_list,
             include_children?: include_children?
           }
         } = socket
       ) do
    tasks = list_tasks_for_location(project, task_list, include_children?)

    socket
    |> assign(:tasks_empty?, tasks == [])
    |> stream(:tasks, tasks, reset: true)
  end

  defp refresh_move_surface(
         %{
           assigns: %{
             selected_project: %Project{} = project,
             task_move: %TaskMove{} = task_move
           }
         } = socket
       ) do
    if TaskMove.active?(task_move) do
      case TaskMove.refresh(task_move, project) do
        {:ok, task_move, _task} ->
          socket
          |> assign(:task_move, task_move)
          |> refresh_task_stream()

        {:error, task_move, :task_not_found} ->
          socket
          |> assign(:task_move, task_move)
          |> refresh_task_stream()
      end
    else
      refresh_task_stream(socket)
    end
  end

  defp refresh_move_surface(socket), do: socket

  defp clear_task_modal_state(socket) do
    socket
    |> clear_transient_task_modal_state()
    |> clear_task_hierarchy()
    |> assign(:task_detail_open?, false)
  end

  defp clear_modal_state_for_action(
         %{assigns: %{task_detail_open?: true}} = socket,
         :show_task
       ) do
    clear_transient_task_modal_state(socket)
  end

  defp clear_modal_state_for_action(socket, _action), do: clear_task_modal_state(socket)

  defp clear_transient_task_modal_state(socket) do
    socket
    |> assign(:selected_task, nil)
    |> assign(:task_not_found?, false)
    |> assign(:task_create_form, nil)
    |> assign(:task_create_enabled?, false)
    |> assign(:task_create_location, nil)
    |> assign(:task_parent_picker, TaskParentPicker.empty())
    |> assign(:task_autosave, TaskAutosave.clear(socket.assigns.task_autosave))
    |> assign(:task_move, TaskMove.clear(socket.assigns.task_move))
  end

  defp clear_task_hierarchy(socket) do
    assign(socket, :task_hierarchy, TaskHierarchy.clear(socket.assigns.task_hierarchy))
  end

  defp task_not_found_modal_state(socket) do
    socket
    |> clear_task_modal_state()
    |> assign(:task_not_found?, true)
  end

  defp reload_task_hierarchy(socket, %Project{} = project, %Task{} = task) do
    case Tasks.get_task_hierarchy(project, task) do
      {:ok, hierarchy} ->
        assign(
          socket,
          :task_hierarchy,
          TaskHierarchy.load(socket.assigns.task_hierarchy, hierarchy)
        )

      {:error, :not_found} ->
        task_not_found_modal_state(socket)
    end
  end

  defp task_move_origin(socket, %Task{id: task_id}) do
    case socket.assigns.selected_task do
      %Task{id: ^task_id} -> :detail
      _not_selected -> :row
    end
  end

  defp flush_move_task_fields(:detail, socket) do
    case flush_task_autosave(socket) do
      {:ok, %{assigns: %{task_autosave: %{form: %{source: %{valid?: true}}}}} = socket} ->
        {:ok, socket}

      {:ok, socket} ->
        {:error, socket}

      {:error, socket} ->
        {:error, socket}
    end
  end

  defp flush_move_task_fields(_origin, socket), do: {:ok, socket}

  defp refresh_selected_task_after_move(socket, task_id) do
    case socket.assigns.selected_task do
      %Task{id: ^task_id} ->
        case Tasks.get_task_for_project(socket.assigns.selected_project, task_id) do
          %Task{} = task ->
            socket
            |> assign(:selected_task, task)
            |> assign(
              :task_autosave,
              TaskAutosave.load(socket.assigns.task_autosave, task, saved?: true)
            )

          nil ->
            socket
        end

      _not_selected ->
        socket
    end
  end

  defp reinsert_active_move_row(
         %{
           assigns: %{
             task_move: %TaskMove{
               active_task: %{origin: :row, task_with_location: %TaskWithLocation{} = task}
             }
           }
         } = socket
       ) do
    stream_insert(socket, :tasks, task)
  end

  defp reinsert_active_move_row(socket), do: socket

  defp flush_task_autosave(
         %{
           assigns: %{
             selected_project: %Project{} = project,
             selected_task: %Task{} = task
           }
         } = socket
       ) do
    case TaskAutosave.flush(socket.assigns.task_autosave, project, task) do
      {:ok, autosave, task} ->
        {:ok, sync_task_autosave(socket, autosave, task)}

      {:error, autosave, task} ->
        {:error, sync_task_autosave(socket, autosave, task)}

      {:not_found, autosave} ->
        {:ok, apply_task_autosave_result(socket, {:not_found, autosave})}
    end
  end

  defp flush_task_autosave(socket), do: {:ok, socket}

  defp restore_failed_task_route(
         %{
           assigns: %{
             selected_project: %Project{} = project,
             selected_task: %Task{} = task,
             selected_list: selected_list,
             include_children?: include_children?
           }
         } = socket,
         params
       ) do
    if selected_task_route?(params, project, selected_list, task, include_children?) do
      socket
    else
      push_patch(socket,
        to: task_detail_path(project, selected_list, task, include_children?),
        replace: true
      )
    end
  end

  defp selected_task_route?(
         %{"project_id" => project_id, "task_id" => task_id} = params,
         %Project{id: selected_project_id},
         selected_list,
         %Task{id: selected_task_id},
         include_children?
       ) do
    project_id == Integer.to_string(selected_project_id) &&
      task_id == Integer.to_string(selected_task_id) &&
      selected_list_route?(params, selected_list) &&
      canonical_query_route?(params, include_children?)
  end

  defp selected_task_route?(_params, _project, _selected_list, _task, _include_children?),
    do: false

  defp selected_list_route?(%{"list_id" => list_id}, %TaskList{id: selected_list_id}) do
    list_id == Integer.to_string(selected_list_id)
  end

  defp selected_list_route?(%{"list_id" => _list_id}, nil), do: false
  defp selected_list_route?(_params, nil), do: true
  defp selected_list_route?(_params, %TaskList{}), do: false

  defp canonical_query_route?(params, include_children?) do
    Map.get(params, "include_children") == if(include_children?, do: "true", else: nil)
  end

  defp browse_path(%Project{id: project_id}, nil, include_children?) do
    append_include_children(~p"/projects/#{project_id}", include_children?)
  end

  defp browse_path(%Project{id: project_id}, %TaskList{id: list_id}, include_children?) do
    append_include_children(~p"/projects/#{project_id}/lists/#{list_id}", include_children?)
  end

  defp task_create_state(project, selected_list, params) do
    case Map.fetch(params, "parent_task_id") do
      :error ->
        {selected_list, TaskParentPicker.open_create(TaskParentPicker.empty(), project, nil)}

      {:ok, parent_task_id} ->
        case task_create_parent(project, parent_task_id) do
          {:ok, parent, location} ->
            {location, TaskParentPicker.open_create(TaskParentPicker.empty(), project, parent)}

          :error ->
            {selected_list,
             TaskParentPicker.empty()
             |> TaskParentPicker.open_create(project, nil)
             |> TaskParentPicker.reject_draft("That parent Task is no longer available.")}
        end
    end
  end

  defp task_create_parent(project, parent_task_id) do
    with %Task{} = parent <- Tasks.get_task_for_project(project, parent_task_id),
         {:ok, location} <- task_location(project, parent) do
      {:ok, parent, location}
    else
      _not_found -> :error
    end
  end

  defp task_location(_project, %Task{list_id: nil}), do: {:ok, nil}

  defp task_location(project, %Task{list_id: list_id}) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> {:ok, task_list}
      nil -> :error
    end
  end

  defp update_task_parent_picker(
         %{assigns: %{live_action: :new_task, task_parent_picker: %TaskParentPicker{}}} = socket,
         _save?,
         transition
       ) do
    assign(socket, :task_parent_picker, transition.(socket.assigns.task_parent_picker))
  end

  defp update_task_parent_picker(
         %{
           assigns: %{
             live_action: :show_task,
             selected_project: %Project{} = project,
             selected_task: %Task{} = task,
             task_parent_picker: %TaskParentPicker{} = picker
           }
         } = socket,
         save?,
         transition
       ) do
    picker = transition.(picker)

    if save? do
      save_task_parent_picker(socket, project, task, picker)
    else
      assign(socket, :task_parent_picker, picker)
    end
  end

  defp update_task_parent_picker(socket, _save?, _transition), do: socket

  defp save_task_parent_picker(socket, project, task, picker) do
    case TaskParentPicker.save_edit(picker, project, task) do
      {:ok, picker, updated_task} ->
        socket
        |> assign(:task_parent_picker, picker)
        |> assign(:selected_task, updated_task)
        |> reload_task_hierarchy(project, updated_task)
        |> refresh_task_stream()

      {:error, picker, _reason} ->
        assign(socket, :task_parent_picker, picker)
    end
  end

  defp new_task_path(project, task_list, include_children?, parent_task_id \\ nil)

  defp new_task_path(%Project{id: project_id}, nil, include_children?, parent_task_id) do
    ~p"/projects/#{project_id}/tasks/new"
    |> append_include_children(include_children?)
    |> append_parent_task_id(parent_task_id)
  end

  defp new_task_path(
         %Project{id: project_id},
         %TaskList{id: list_id},
         include_children?,
         parent_task_id
       ) do
    ~p"/projects/#{project_id}/lists/#{list_id}/tasks/new"
    |> append_include_children(include_children?)
    |> append_parent_task_id(parent_task_id)
  end

  defp task_detail_path(
         %Project{id: project_id},
         nil,
         %Task{id: task_id},
         include_children?
       ) do
    append_include_children(~p"/projects/#{project_id}/tasks/#{task_id}", include_children?)
  end

  defp task_detail_path(
         %Project{id: project_id},
         %TaskList{id: list_id},
         %Task{id: task_id},
         include_children?
       ) do
    append_include_children(
      ~p"/projects/#{project_id}/lists/#{list_id}/tasks/#{task_id}",
      include_children?
    )
  end

  defp append_include_children(path, true), do: path <> "?include_children=true"
  defp append_include_children(path, false), do: path

  defp append_parent_task_id(path, nil), do: path

  defp append_parent_task_id(path, parent_task_id) do
    separator = if String.contains?(path, "?"), do: "&", else: "?"
    path <> separator <> "parent_task_id=" <> Integer.to_string(parent_task_id)
  end

  defp task_create_location_copy(%Project{name: project_name}, nil) do
    "Create this Task in Project #{project_name}."
  end

  defp task_create_location_copy(%Project{}, %TaskList{name: list_name}) do
    "Create this Task in List #{list_name}."
  end

  defp project_form(project), do: to_form(Projects.change_project(project))
end
