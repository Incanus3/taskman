defmodule TaskmanWeb.API.TaskSearchController do
  use TaskmanWeb, :controller

  action_fallback TaskmanWeb.API.FallbackController

  alias Taskman.Projects
  alias Taskman.Tasks
  alias TaskmanWeb.API.Params

  def index(conn, _params) do
    with {:ok, query, project_id} <- parse_query(conn.query_string),
         {:ok, project} <- resolve_project(project_id) do
      json(conn, %{data: Tasks.search_tasks(query, project)})
    end
  end

  defp parse_query(query_string) do
    pairs = Enum.to_list(URI.query_decoder(query_string))
    keys = Enum.map(pairs, &elem(&1, 0))

    if length(keys) != length(Enum.uniq(keys)) or
         Enum.any?(keys, &(&1 not in ["q", "project_id"])) do
      {:error, :invalid_request}
    else
      params = Map.new(pairs)
      query = Map.get(params, "q")
      project_id = Map.get(params, "project_id")

      if is_binary(query) and String.trim(query) != "" do
        {:ok, query, project_id}
      else
        {:error, :invalid_request}
      end
    end
  end

  defp resolve_project(nil), do: {:ok, nil}

  defp resolve_project(raw_id) do
    with true <- Regex.match?(~r/^[0-9]+$/, raw_id),
         {:ok, id} <- Params.positive_id(raw_id) do
      case Projects.get_project(id) do
        nil -> {:error, :not_found}
        project -> {:ok, project}
      end
    else
      _ -> {:error, :invalid_request}
    end
  end
end
