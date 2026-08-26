defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation
  alias TaskmanWeb.{TaskComponents, TaskForm, WorkspaceNavigation}

  @editable_task_fields ~w(title description status priority due_at)
  @debounced_task_fields ~w(title description)

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
     |> assign(:active_move_task, nil)
     |> assign(:move_query, "")
     |> assign(:move_destination, nil)
     |> assign(:move_options, [])
     |> assign(:move_options_open?, false)
     |> assign(:move_error, nil)
     |> assign(:expanded_node_ids, MapSet.new())
     |> assign(:active_list_project, nil)
     |> assign(:active_list_form, nil)
     |> assign(:list_form, nil)
     |> stream(:projects, Projects.list_projects())
     |> stream(:tasks, [])
     |> stream(:navigation_nodes, [])
     |> refresh_navigation_stream()}
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
          |> clear_task_modal_state()
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

  defp apply_action(:new_task, _params, socket) do
    changeset = Tasks.change_task(socket.assigns.selected_project)

    socket
    |> assign(:task_form, to_form(changeset))
    |> assign(:task_create_enabled?, changeset.valid?)
  end

  defp apply_action(:show_task, %{"task_id" => task_id}, socket) do
    case Tasks.get_task_for_project(socket.assigns.selected_project, task_id) do
      %Task{} = task ->
        socket
        |> assign(:selected_task, task)
        |> assign(:task_form, to_form(Tasks.change_task(task)))

      nil ->
        assign(socket, :task_not_found?, true)
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
     |> assign(:active_list_project, nil)
     |> assign(:active_list_form, nil)
     |> assign(:list_form, nil)
     |> refresh_navigation_stream()}
  end

  def handle_event("validate_list", %{"list" => list_params}, socket) do
    case list_form_target(socket) do
      {:ok, active_list_form, task_list} ->
        changeset =
          task_list
          |> Lists.change_list(list_params)
          |> Map.put(:action, :validate)

        {:noreply,
         socket
         |> assign(:active_list_form, active_list_form)
         |> assign(:list_form, to_form(changeset, as: :list))
         |> refresh_navigation_stream()}

      :error ->
        {:noreply, socket}
    end
  end

  def handle_event("save_list", %{"list" => list_params}, socket) do
    case {socket.assigns.active_list_project, list_form_target(socket)} do
      {%Project{} = project, {:ok, {:new, parent}, _task_list}} ->
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
             |> assign(:active_list_project, nil)
             |> assign(:active_list_form, nil)
             |> assign(:list_form, nil)
             |> refresh_navigation_stream()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_form_error(socket, changeset)}

          {:error, :not_found} ->
            {:noreply, socket}
        end

      {%Project{} = project, {:ok, {:rename, current_list}, _task_list}} ->
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
             |> assign(:active_list_project, nil)
             |> assign(:active_list_form, nil)
             |> assign(:list_form, nil)
             |> refresh_navigation_stream()
             |> refresh_task_stream()
             |> refresh_move_surface()}

          {:error, %Ecto.Changeset{} = changeset} ->
            {:noreply, assign_list_form_error(socket, changeset)}

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
     |> assign(:task_form, to_form(changeset))
     |> assign(:task_create_enabled?, changeset.valid?)}
  end

  def handle_event("save_task", %{"task" => task_params}, socket) do
    case Tasks.create_task(
           socket.assigns.selected_project,
           socket.assigns.selected_list,
           task_params
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

  def handle_event("open_move_task", %{"task-id" => task_id}, socket) do
    case socket.assigns.selected_project do
      %Project{} = project ->
        case Tasks.get_task_for_project(project, task_id) do
          %Task{} = task ->
            active_move_task = active_move_task(socket, project, task)

            {:noreply,
             socket
             |> assign(:active_move_task, active_move_task)
             |> assign(:move_query, "")
             |> assign(:move_destination, nil)
             |> assign(:move_options, move_destination_options(project, task))
             |> assign(:move_options_open?, false)
             |> assign(:move_error, nil)
             |> refresh_move_surface()}

          nil ->
            {:noreply, clear_move_state(socket)}
        end

      nil ->
        {:noreply, clear_move_state(socket)}
    end
  end

  def handle_event("open_move_task", _params, socket), do: {:noreply, clear_move_state(socket)}

  def handle_event("open_move_destinations", _params, socket) do
    {:noreply,
     socket
     |> assign(:move_options_open?, true)
     |> refresh_move_surface()}
  end

  def handle_event("search_move_destinations", %{"value" => query}, socket)
      when is_binary(query) do
    destination =
      if query == socket.assigns.move_query, do: socket.assigns.move_destination, else: nil

    {:noreply,
     socket
     |> assign(:move_query, query)
     |> assign(:move_destination, destination)
     |> assign(:move_options_open?, true)
     |> refresh_move_surface()}
  end

  def handle_event("search_move_destinations", _params, socket), do: {:noreply, socket}

  def handle_event("select_move_destination", %{"destination" => destination}, socket)
      when is_binary(destination) do
    query =
      Enum.find_value(socket.assigns.move_options, socket.assigns.move_query, fn option ->
        if option.value == destination, do: option.label
      end)

    {:noreply,
     socket
     |> assign(:move_query, query)
     |> assign(:move_destination, destination)
     |> assign(:move_options_open?, false)
     |> assign(:move_error, nil)
     |> refresh_move_surface()}
  end

  def handle_event("select_move_destination", _params, socket), do: {:noreply, socket}

  def handle_event("cancel_move_task", _params, socket),
    do: {:noreply, socket |> clear_move_state() |> refresh_move_surface()}

  def handle_event(
        "submit_move_task",
        _params,
        %{
          assigns: %{
            active_move_task: %{task_id: task_id, origin: origin},
            selected_project: %Project{} = project
          }
        } =
          socket
      ) do
    case flush_move_task_fields(origin, socket) do
      {:error, socket} ->
        {:noreply, assign_move_error(socket, "Save the Task before moving it.")}

      {:ok, socket} ->
        submit_task_move(socket, project, task_id)
    end
  end

  def handle_event("submit_move_task", _params, socket), do: {:noreply, socket}

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
        task_list = %TaskList{project_id: project.id, parent_list_id: parent && parent.id}

        {:noreply,
         socket
         |> assign(:active_list_project, project)
         |> assign(:active_list_form, {:new, parent})
         |> assign(:list_form, list_form(task_list))
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
         |> assign(:active_list_project, project)
         |> assign(:active_list_form, {:rename, task_list})
         |> assign(:list_form, list_form(task_list))
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

  defp list_form_target(%{
         assigns: %{active_list_project: %Project{} = project, active_list_form: {:new, parent}}
       }) do
    case parent do
      nil ->
        {:ok, {:new, nil}, %TaskList{project_id: project.id}}

      %TaskList{id: parent_id} ->
        case Lists.get_list_for_project(project, parent_id) do
          %TaskList{} = fresh_parent ->
            {:ok, {:new, fresh_parent},
             %TaskList{project_id: project.id, parent_list_id: fresh_parent.id}}

          nil ->
            :error
        end
    end
  end

  defp list_form_target(%{
         assigns: %{
           active_list_project: %Project{} = project,
           active_list_form: {:rename, task_list}
         }
       }) do
    case task_list do
      %TaskList{id: list_id} ->
        case Lists.get_list_for_project(project, list_id) do
          %TaskList{} = fresh_list -> {:ok, {:rename, fresh_list}, fresh_list}
          nil -> :error
        end

      _invalid ->
        :error
    end
  end

  defp list_form_target(_socket), do: :error

  defp assign_list_form_error(socket, changeset) do
    socket
    |> assign(:list_form, to_form(Map.put(changeset, :action, :validate), as: :list))
    |> refresh_navigation_stream()
  end

  defp list_form(task_list, attrs \\ %{}) do
    task_list
    |> Lists.change_list(attrs)
    |> to_form(as: :list)
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
    |> assign(:active_list_project, nil)
    |> assign(:active_list_form, nil)
    |> assign(:list_form, nil)
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
             active_move_task: %{task_id: task_id} = active_move_task
           }
         } = socket
       ) do
    case Tasks.get_task_for_project(project, task_id) do
      %Task{} = task ->
        all_options = move_destination_options(project, task)

        active_move_task =
          active_move_task
          |> Map.put(:current_destination, task_destination(task))
          |> refresh_active_move_task_location(project, task)

        destination =
          if Enum.any?(all_options, &(&1.value == socket.assigns.move_destination)) do
            socket.assigns.move_destination
          else
            nil
          end

        socket
        |> assign(:active_move_task, active_move_task)
        |> assign(:move_destination, destination)
        |> assign(:move_options, filtered_move_options(socket, all_options))
        |> assign(:move_error, nil)
        |> refresh_task_stream()

      nil ->
        socket
        |> clear_move_state()
        |> refresh_task_stream()
    end
  end

  defp refresh_move_surface(%{assigns: %{selected_project: %Project{}}} = socket),
    do: refresh_task_stream(socket)

  defp refresh_move_surface(socket), do: socket

  defp refresh_active_move_task_location(%{origin: "row"} = active_move_task, project, task),
    do: Map.put(active_move_task, :task_with_location, task_with_location(project, task))

  defp refresh_active_move_task_location(active_move_task, _project, _task), do: active_move_task

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
    |> clear_move_state()
  end

  defp clear_move_state(socket) do
    socket
    |> assign(:active_move_task, nil)
    |> assign(:move_query, "")
    |> assign(:move_destination, nil)
    |> assign(:move_options, [])
    |> assign(:move_options_open?, false)
    |> assign(:move_error, nil)
  end

  defp move_origin(socket, %Task{id: task_id}) do
    case socket.assigns.selected_task do
      %Task{id: ^task_id} -> "detail"
      _not_selected -> "row"
    end
  end

  defp active_move_task(socket, project, task) do
    active_move_task = %{
      task_id: task.id,
      origin: move_origin(socket, task),
      current_destination: task_destination(task)
    }

    if active_move_task.origin == "row" do
      Map.put(active_move_task, :task_with_location, task_with_location(project, task))
    else
      active_move_task
    end
  end

  defp task_destination(%Task{list_id: nil}), do: "project"
  defp task_destination(%Task{list_id: list_id}), do: "list:#{list_id}"

  defp task_with_location(project, task) do
    task_lists = Lists.list_lists_for_project(project)

    location_path =
      case Enum.find(task_lists, &(&1.id == task.list_id)) do
        nil -> []
        task_list -> Lists.path_for(task_lists, task_list)
      end

    %TaskWithLocation{task: task, location_path: location_path}
  end

  defp move_destination_options(project, task) do
    task_lists = Lists.list_lists_for_project(project)

    [
      %{
        id: project.id,
        value: "project",
        label: "Project · #{project.name}",
        current?: is_nil(task.list_id)
      }
      | Enum.map(ordered_task_lists(task_lists), fn task_list ->
          %{
            id: task_list.id,
            value: "list:#{task_list.id}",
            label:
              task_lists
              |> Lists.path_for(task_list)
              |> Enum.map_join(" / ", & &1.name),
            current?: task.list_id == task_list.id
          }
        end)
    ]
  end

  defp ordered_task_lists(task_lists) do
    children_by_parent = Enum.group_by(task_lists, & &1.parent_list_id)

    children_by_parent
    |> Map.get(nil, [])
    |> Enum.flat_map(&task_list_preorder(&1, children_by_parent))
  end

  defp task_list_preorder(task_list, children_by_parent) do
    [
      task_list
      | Enum.flat_map(
          Map.get(children_by_parent, task_list.id, []),
          &task_list_preorder(&1, children_by_parent)
        )
    ]
  end

  defp filtered_move_options(socket, options) do
    Enum.filter(options, fn option ->
      String.contains?(
        String.downcase(option.label),
        String.downcase(socket.assigns.move_query)
      )
    end)
  end

  defp flush_move_task_fields("detail", socket) do
    case flush_dirty_task_fields(socket) do
      {:ok, %{assigns: %{task_form: %{source: %{valid?: true}}}} = socket} -> {:ok, socket}
      {:ok, socket} -> {:error, socket}
      {:error, socket} -> {:error, socket}
    end
  end

  defp flush_move_task_fields(_origin, socket), do: {:ok, socket}

  defp submit_task_move(socket, project, task_id) do
    case Tasks.get_task_for_project(project, task_id) do
      nil ->
        {:noreply, assign_move_error(socket, "This Task is no longer available.")}

      %Task{} = task ->
        case resolve_move_destination(project, socket.assigns.move_destination) do
          {:error, :not_found} ->
            {:noreply, assign_move_error(socket, "That destination is no longer available.")}

          {:ok, destination} ->
            complete_task_move(socket, project, task, destination)
        end
    end
  end

  defp resolve_move_destination(_project, "project"), do: {:ok, nil}

  defp resolve_move_destination(project, "list:" <> list_id) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = destination -> {:ok, destination}
      nil -> {:error, :not_found}
    end
  end

  defp resolve_move_destination(_project, _destination), do: {:error, :not_found}

  defp complete_task_move(socket, project, task, destination) do
    case Tasks.move_task(project, task, destination) do
      {:ok, _moved_task} ->
        {:noreply,
         socket
         |> refresh_selected_task_after_move(task.id)
         |> refresh_task_stream()
         |> clear_move_state()}

      {:error, :unchanged_location} ->
        {:noreply,
         socket
         |> refresh_move_surface()
         |> assign_move_error("This Task is already in that location.")}

      {:error, :not_found} ->
        {:noreply, assign_move_error(socket, "That destination is no longer available.")}

      {:error, _reason} ->
        {:noreply, assign_move_error(socket, "Couldn’t move this Task. Please try again.")}
    end
  end

  defp refresh_selected_task_after_move(socket, task_id) do
    case socket.assigns.selected_task do
      %Task{id: ^task_id} ->
        case Tasks.get_task_for_project(socket.assigns.selected_project, task_id) do
          %Task{} = task ->
            socket
            |> assign(:selected_task, task)
            |> assign(:task_form, to_form(Tasks.change_task(task)))
            |> assign(:task_draft, %{})
            |> assign(:task_dirty_fields, MapSet.new())
            |> assign(:task_revisions, %{})
            |> assign(:task_save_failed?, false)
            |> assign(:task_saved?, true)
            |> refresh_task_save_state()

          nil ->
            socket
        end

      _not_selected ->
        socket
    end
  end

  defp assign_move_error(socket, message) do
    socket
    |> assign(:move_error, message)
    |> reinsert_active_move_row()
  end

  defp reinsert_active_move_row(
         %{assigns: %{active_move_task: %{origin: "row", task_with_location: task_with_location}}} =
           socket
       ) do
    stream_insert(socket, :tasks, task_with_location)
  end

  defp reinsert_active_move_row(socket), do: socket

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
        |> refresh_task_stream()
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

  defp browse_path(%Project{id: project_id}, nil, include_children?) do
    append_include_children(~p"/projects/#{project_id}", include_children?)
  end

  defp browse_path(%Project{id: project_id}, %TaskList{id: list_id}, include_children?) do
    append_include_children(~p"/projects/#{project_id}/lists/#{list_id}", include_children?)
  end

  defp new_task_path(%Project{id: project_id}, nil, include_children?) do
    append_include_children(~p"/projects/#{project_id}/tasks/new", include_children?)
  end

  defp new_task_path(%Project{id: project_id}, %TaskList{id: list_id}, include_children?) do
    append_include_children(
      ~p"/projects/#{project_id}/lists/#{list_id}/tasks/new",
      include_children?
    )
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

  defp project_form(project), do: to_form(Projects.change_project(project))
end
