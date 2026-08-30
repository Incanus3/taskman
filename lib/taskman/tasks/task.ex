defmodule Taskman.Tasks.Task do
  use Ecto.Schema

  import Ecto.Changeset

  @statuses [:icebox, :pending, :in_progress, :in_review, :done, :will_not_do]
  @priorities [:none, :low, :medium, :high, :urgent]

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          project_id: pos_integer() | nil,
          list_id: pos_integer() | nil,
          parent_task_id: pos_integer() | nil,
          title: String.t() | nil,
          description: String.t() | nil,
          status: atom(),
          priority: atom(),
          due_at: NaiveDateTime.t() | nil
        }

  schema "tasks" do
    field :title, :string
    field :description, :string, default: ""
    field :status, Ecto.Enum, values: @statuses, default: :pending
    field :priority, Ecto.Enum, values: @priorities, default: :none
    field :due_at, :naive_datetime

    belongs_to :project, Taskman.Projects.Project
    belongs_to :list, Taskman.Lists.TaskList
    belongs_to :parent_task, __MODULE__

    timestamps(type: :utc_datetime)
  end

  def statuses, do: @statuses
  def priorities, do: @priorities

  def changeset(task, attrs) do
    task
    |> cast(attrs, [:title, :description, :status, :priority, :due_at])
    |> update_change(:title, fn title -> title && String.trim(title) end)
    |> validate_required([:project_id, :title, :status, :priority])
    |> foreign_key_constraint(:project_id)
    |> foreign_key_constraint(:list_id, name: :tasks_list_id_project_id_fkey)
    |> foreign_key_constraint(:parent_task_id,
      name: :tasks_parent_task_id_project_id_fkey,
      message: "does not exist"
    )
    |> check_constraint(:status, name: :tasks_status_check)
    |> check_constraint(:priority, name: :tasks_priority_check)
    |> check_constraint(:parent_task_id,
      name: :tasks_parent_not_self_check,
      message: "cannot be its own parent"
    )
  end
end
