defmodule Taskman.Tasks.AvailabilityPersistence do
  @moduledoc false

  import Ecto.Query

  alias Taskman.Repo
  alias Taskman.Tasks.Task

  @spec list_location_states(pos_integer(), [atom()]) :: [{pos_integer() | nil, atom()}]
  def list_location_states(project_id, additional_states) do
    from(task in Task,
      where: task.project_id == ^project_id and task.status in ^additional_states,
      distinct: true,
      select: {task.list_id, task.status}
    )
    |> Repo.all()
  end
end
