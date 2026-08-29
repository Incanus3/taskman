defmodule TaskmanWeb.API.ProjectController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Projects
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  def index(conn, _params) do
    projects = Projects.list_projects()

    json(conn, %{data: Enum.map(projects, &Representation.project/1)})
  end

  def create(conn, %{"project" => attrs}) when is_map(attrs) do
    case Projects.create_project(attrs) do
      {:ok, project} ->
        conn
        |> put_status(:created)
        |> json(%{data: Representation.project(project)})

      {:error, reason} ->
        {:error, reason}
    end
  end

  def create(_conn, _params), do: {:error, :invalid_request}

  def show(conn, %{"project_id" => project_id}) do
    case Params.positive_id(project_id) do
      {:ok, id} ->
        case Projects.get_project(id) do
          nil -> {:error, :not_found}
          project -> json(conn, %{data: Representation.project(project)})
        end

      {:error, :invalid_request} ->
        {:error, :invalid_request}
    end
  end
end
