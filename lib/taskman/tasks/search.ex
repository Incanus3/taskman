defmodule Taskman.Tasks.Search do
  import Ecto.Query

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task

  @result_limit 20

  @doc "Searches Tasks across Projects, or within one Project, for navigation summaries."
  def search_tasks(query, project_or_nil) when is_binary(query) do
    if String.trim(query) == "" do
      []
    else
      query
      |> base_query(project_or_nil)
      |> order_by(
        [task, project],
        asc:
          fragment(
            "CASE WHEN CAST(? AS text) = ? THEN 0 ELSE 1 END",
            task.id,
            ^String.trim(query)
          ),
        asc: fragment("lower(?)", task.title),
        asc: fragment("lower(?)", project.name),
        asc: task.id
      )
      |> limit(^@result_limit)
      |> select([task, project], {task, project.name})
      |> summaries()
    end
  end

  @doc "Returns the first page of link candidates in the chosen Project."
  def blocking_candidates(%Project{} = project, %Task{id: selected_id}, query)
      when is_binary(query) do
    query
    |> base_query(project)
    |> where([task], task.id != ^selected_id)
    |> order_by(
      [task],
      asc:
        fragment("CASE WHEN CAST(? AS text) = ? THEN 0 ELSE 1 END", task.id, ^String.trim(query)),
      asc: fragment("lower(?)", task.title),
      asc: task.id
    )
    |> limit(^@result_limit)
    |> select([task, project], {task, project.name})
    |> summaries()
  end

  defp base_query(query, project_or_nil) do
    Task
    |> join(:inner, [task], project in Project, on: task.project_id == project.id)
    |> project_scope(project_or_nil)
    |> filter_by_terms(query)
  end

  defp project_scope(query, nil), do: query

  defp project_scope(query, %Project{id: project_id}) do
    where(query, [task], task.project_id == ^project_id)
  end

  defp summaries(query) do
    rows = Repo.all(query)
    project_ids = rows |> Enum.map(fn {task, _} -> task.project_id end) |> Enum.uniq()

    lists =
      if project_ids == [] do
        []
      else
        TaskList
        |> where([list], list.project_id in ^project_ids)
        |> Repo.all()
      end

    lists_by_id = Map.new(lists, &{&1.id, &1})

    Enum.map(rows, fn {task, project_name} ->
      path =
        lists
        |> Lists.path_for(Map.get(lists_by_id, task.list_id))
        |> Enum.map(& &1.name)

      %{
        id: task.id,
        title: task.title,
        status: task.status,
        priority: task.priority,
        project_id: task.project_id,
        project_name: project_name,
        location: %{
          kind: if(is_nil(task.list_id), do: "project", else: "list"),
          list_id: task.list_id,
          path: path
        }
      }
    end)
  end

  @doc """
  Filters Tasks by Unicode whitespace-separated terms found in the decimal ID or title.

  Each term may match either field; all terms must match. An empty query leaves
  the caller's query unchanged so callers can choose their own blank behavior.
  """
  @spec filter_by_terms(Ecto.Queryable.t(), String.t()) :: Ecto.Query.t()
  def filter_by_terms(queryable, query) when is_binary(query) do
    query
    |> String.split(~r/\s+/u, trim: true)
    |> Enum.reduce(queryable, fn term, tasks ->
      title_term = String.downcase(term)

      where(
        tasks,
        [task],
        fragment("strpos(CAST(? AS text), ?) > 0", task.id, ^term) or
          fragment("strpos(lower(?), ?) > 0", task.title, ^title_term)
      )
    end)
  end
end
