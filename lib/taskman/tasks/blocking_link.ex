defmodule Taskman.Tasks.BlockingLink do
  @moduledoc false

  use Ecto.Schema

  import Ecto.Changeset

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          blocking_task_id: pos_integer() | nil,
          blocked_task_id: pos_integer() | nil,
          inserted_at: DateTime.t() | nil,
          updated_at: DateTime.t() | nil
        }

  schema "task_blocking_links" do
    belongs_to :blocking_task, Taskman.Tasks.Task
    belongs_to :blocked_task, Taskman.Tasks.Task

    timestamps(type: :utc_datetime)
  end

  def changeset(link) do
    link
    |> change()
    |> validate_required([:blocking_task_id, :blocked_task_id])
    |> unique_constraint([:blocking_task_id, :blocked_task_id],
      name: :task_blocking_links_blocking_task_id_blocked_task_id_index
    )
    |> check_constraint(:blocked_task_id, name: :task_blocking_links_not_self_check)
    |> foreign_key_constraint(:blocking_task_id)
    |> foreign_key_constraint(:blocked_task_id)
  end
end
