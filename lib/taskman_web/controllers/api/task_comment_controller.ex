defmodule TaskmanWeb.API.TaskCommentController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.API.Params
  alias TaskmanWeb.API.Representation

  def index(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    conn = fetch_query_params(conn)

    with :ok <- validate_no_query(conn.query_params),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, comments} <- Tasks.list_comments(project, task) do
      json(conn, %{data: Enum.map(comments, &Representation.comment/1)})
    end
  end

  def index(_conn, _params), do: {:error, :invalid_request}

  def create(conn, %{"project_id" => project_id, "task_id" => task_id}) do
    conn = fetch_query_params(conn)

    with :ok <- validate_no_query(conn.query_params),
         {:ok, attrs} <- comment_attrs(conn.body_params),
         {:ok, project} <- fetch_project(project_id),
         {:ok, task} <- fetch_task(project, task_id),
         {:ok, comment} <- Tasks.create_comment(project, task, conn.assigns.current_user, attrs) do
      conn
      |> put_status(:created)
      |> json(%{data: Representation.comment(comment)})
    end
  end

  def create(_conn, _params), do: {:error, :invalid_request}

  defp validate_no_query(%{} = query) when map_size(query) == 0, do: :ok
  defp validate_no_query(_query), do: {:error, :invalid_request}

  defp comment_attrs(%{"comment" => attrs} = body)
       when map_size(body) == 1 and is_map(attrs) do
    if Enum.all?(Map.keys(attrs), &(&1 in ["text", "author_name"])) and
         valid_string_field?(attrs, "text") and
         valid_string_field?(attrs, "author_name") do
      {:ok, Map.take(attrs, ["text", "author_name"])}
    else
      {:error, :invalid_request}
    end
  end

  defp comment_attrs(_body), do: {:error, :invalid_request}

  defp valid_string_field?(attrs, key) do
    not Map.has_key?(attrs, key) or is_binary(Map.get(attrs, key))
  end

  defp fetch_project(project_id) do
    with {:ok, id} <- Params.positive_id(project_id) do
      case Projects.get_project(id) do
        nil -> {:error, :not_found}
        project -> {:ok, project}
      end
    end
  end

  defp fetch_task(project, task_id) do
    with {:ok, id} <- Params.positive_id(task_id) do
      case Tasks.get_task_for_project(project, id) do
        nil -> {:error, :not_found}
        task -> {:ok, task}
      end
    end
  end
end
