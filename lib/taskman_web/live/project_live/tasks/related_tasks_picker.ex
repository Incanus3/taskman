defmodule TaskmanWeb.ProjectLive.Tasks.RelatedTasksPicker do
  @moduledoc "State for the Project-scoped relationship candidate picker."

  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  defstruct direction: nil,
            project_id: nil,
            projects: [],
            query: "",
            candidates: [],
            project_form: nil,
            search_form: nil,
            error: nil

  @type t :: %__MODULE__{
          direction: :blocks | :blocked_by | nil,
          project_id: pos_integer() | nil,
          projects: [Project.t()],
          query: String.t(),
          candidates: [map()],
          project_form: Phoenix.HTML.Form.t() | nil,
          search_form: Phoenix.HTML.Form.t() | nil,
          error: String.t() | nil
        }

  def empty, do: %__MODULE__{}

  def open(direction, %Project{} = project, %Task{} = task, projects)
      when direction in [:blocks, :blocked_by] do
    %__MODULE__{
      direction: direction,
      project_id: project.id,
      projects: projects,
      candidates: Tasks.search_blocking_candidates(project, task, "")
    }
    |> with_forms()
  end

  def select_project(%__MODULE__{direction: direction} = picker, project_id, %Task{} = task)
      when direction in [:blocks, :blocked_by] and is_binary(project_id) do
    case Enum.find(picker.projects, &(Integer.to_string(&1.id) == project_id)) do
      %Project{} = project ->
        %{
          picker
          | project_id: project.id,
            query: "",
            error: nil,
            candidates: Tasks.search_blocking_candidates(project, task, "")
        }
        |> with_forms()

      nil ->
        %{picker | error: "Choose an available Project."}
    end
  end

  def select_project(picker, _project_id, _task), do: picker

  def search(%__MODULE__{direction: direction} = picker, query, %Task{} = task)
      when direction in [:blocks, :blocked_by] and is_binary(query) do
    case Enum.find(picker.projects, &(&1.id == picker.project_id)) do
      %Project{} = project ->
        %{
          picker
          | query: query,
            error: nil,
            candidates: Tasks.search_blocking_candidates(project, task, query)
        }
        |> with_forms()

      nil ->
        %{picker | error: "Choose an available Project."}
    end
  end

  def search(picker, _query, _task), do: picker

  def candidate(%__MODULE__{} = picker, id) when is_binary(id),
    do: Enum.find(picker.candidates, &(Integer.to_string(&1.id) == id))

  def candidate(%__MODULE__{}, _id), do: nil

  def reject(%__MODULE__{} = picker, message), do: %{picker | error: message}

  defp with_forms(picker) do
    %{
      picker
      | project_form:
          Phoenix.Component.to_form(%{"project_id" => to_string(picker.project_id)},
            as: :related_project
          ),
        search_form: Phoenix.Component.to_form(%{"query" => picker.query}, as: :related_search)
    }
  end
end
