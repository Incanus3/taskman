defmodule TaskmanWeb.ProjectLive do
  use TaskmanWeb, :live_view

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(:selected_project, nil)
     |> assign(:project_not_found?, false)
     |> assign(:project_form, project_form(%Project{}))
     |> stream(:projects, Projects.list_projects())
     |> stream(:tasks, [])}
  end

  @impl true
  def handle_params(_params, _uri, %{assigns: %{live_action: :index}} = socket) do
    {:noreply, assign_project_state(socket, nil, false, [])}
  end

  def handle_params(%{"project_id" => project_id}, _uri, socket) do
    case Projects.get_project(project_id) do
      %Project{} = project ->
        {:noreply,
         assign_project_state(socket, project, false, Tasks.list_tasks_for_project(project))}

      nil ->
        {:noreply, assign_project_state(socket, nil, true, [])}
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

  defp assign_project_state(socket, selected_project, project_not_found?, tasks) do
    socket
    |> assign(:selected_project, selected_project)
    |> assign(:project_not_found?, project_not_found?)
    |> stream(:projects, Projects.list_projects(), reset: true)
    |> stream(:tasks, tasks, reset: true)
  end

  defp project_form(project), do: to_form(Projects.change_project(project))
end
