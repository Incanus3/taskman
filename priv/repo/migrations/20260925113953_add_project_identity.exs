defmodule Taskman.Repo.Migrations.AddProjectIdentity do
  use Ecto.Migration

  def change do
    alter table(:projects) do
      add :description, :text, null: false, default: ""
      add :icon, :string, null: false, default: "briefcase"
      add :color, :string, null: false, default: "#6366F1"
    end
  end
end
