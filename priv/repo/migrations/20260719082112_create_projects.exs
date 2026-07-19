defmodule Taskman.Repo.Migrations.CreateProjects do
  use Ecto.Migration

  def change do
    create table(:projects) do
      add :name, :string, null: false
      add :primary_directory, :string, null: false

      timestamps(type: :utc_datetime)
    end
  end
end
