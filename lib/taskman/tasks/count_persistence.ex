defmodule Taskman.Tasks.CountPersistence do
  @moduledoc false

  import Ecto.Query

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task

  @type row :: %{
          list_id: pos_integer() | nil,
          parent_list_id: pos_integer() | nil,
          visible_count: non_neg_integer(),
          eligible_count: non_neg_integer()
        }

  @spec list_location_counts(pos_integer(), [atom()], [atom()]) :: [row()]
  def list_location_counts(project_id, visible_statuses, eligible_statuses) do
    task_counts =
      from task in Task,
        where: task.project_id == ^project_id and task.status in ^eligible_statuses,
        group_by: task.list_id,
        select: %{
          list_id: task.list_id,
          visible_count: filter(count(task.id), task.status in ^visible_statuses),
          eligible_count: count(task.id)
        }

    project_location =
      from project in Project,
        where: project.id == ^project_id,
        select: %{list_id: type(^nil, :id), parent_list_id: type(^nil, :id)}

    locations =
      from task_list in TaskList,
        where: task_list.project_id == ^project_id,
        select: %{list_id: task_list.id, parent_list_id: task_list.parent_list_id},
        union_all: ^project_location

    from(location in subquery(locations),
      left_join: counts in subquery(task_counts),
      on: fragment("? IS NOT DISTINCT FROM ?", location.list_id, counts.list_id),
      select: %{
        list_id: location.list_id,
        parent_list_id: location.parent_list_id,
        visible_count: coalesce(counts.visible_count, 0),
        eligible_count: coalesce(counts.eligible_count, 0)
      }
    )
    |> Repo.all()
  end
end
