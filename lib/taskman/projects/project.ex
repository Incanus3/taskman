defmodule Taskman.Projects.Project do
  use Ecto.Schema

  import Ecto.Changeset

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          name: String.t() | nil,
          primary_directory: String.t() | nil
        }

  schema "projects" do
    field :name, :string
    field :primary_directory, :string

    timestamps(type: :utc_datetime)
  end

  def changeset(project, attrs) do
    project
    |> cast(attrs, [:name, :primary_directory])
    |> update_change(:name, &String.trim/1)
    |> validate_required([:name, :primary_directory])
  end
end
