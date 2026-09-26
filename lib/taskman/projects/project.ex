defmodule Taskman.Projects.Project do
  use Ecto.Schema

  import Ecto.Changeset

  @icons ~w(check-circle folder briefcase code-bracket rocket-launch beaker light-bulb wrench-screwdriver)

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          name: String.t() | nil,
          description: String.t(),
          icon: String.t(),
          color: String.t()
        }

  schema "projects" do
    field :name, :string
    field :description, :string, default: ""
    field :icon, :string, default: "briefcase"
    field :color, :string, default: "#6366F1"

    timestamps(type: :utc_datetime)
  end

  @spec icons() :: [String.t()]
  def icons, do: @icons

  def changeset(project, attrs) do
    project
    |> cast(attrs, [:name, :description, :icon, :color])
    |> update_change(:name, &trim_if_string/1)
    |> update_change(:description, &trim_if_string/1)
    |> update_change(:color, &upcase_if_string/1)
    |> validate_required([:name, :icon, :color])
    |> validate_description_not_nil()
    |> validate_length(:description, max: 160)
    |> validate_inclusion(:icon, @icons)
    |> validate_format(:color, ~r/^#[0-9A-F]{6}$/)
  end

  defp trim_if_string(value) when is_binary(value), do: String.trim(value)
  defp trim_if_string(value), do: value

  defp upcase_if_string(value) when is_binary(value), do: String.upcase(value)
  defp upcase_if_string(value), do: value

  defp validate_description_not_nil(changeset) do
    if get_field(changeset, :description) == nil do
      add_error(changeset, :description, "can't be nil")
    else
      changeset
    end
  end
end
