defmodule Taskman.Lists.TaskList do
  use Ecto.Schema

  import Ecto.Changeset

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          project_id: pos_integer() | nil,
          parent_list_id: pos_integer() | nil,
          name: String.t() | nil,
          inserted_at: DateTime.t() | nil,
          updated_at: DateTime.t() | nil
        }

  schema "lists" do
    field :name, :string

    belongs_to :project, Taskman.Projects.Project
    belongs_to :parent_list, __MODULE__

    timestamps(type: :utc_datetime)
  end

  def changeset(task_list, attrs) do
    task_list
    |> cast(attrs, [:name])
    |> update_change(:name, &String.trim/1)
    |> validate_required([:project_id, :name])
    |> validate_length(:name, max: 255)
    |> foreign_key_constraint(:project_id)
    |> foreign_key_constraint(:parent_list_id,
      name: :lists_parent_list_id_project_id_fkey
    )
    |> unique_constraint(:name, name: :lists_sibling_name_index)
  end
end
