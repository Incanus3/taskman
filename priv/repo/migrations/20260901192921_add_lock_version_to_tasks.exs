defmodule Taskman.Repo.Migrations.AddLockVersionToTasks do
  use Ecto.Migration

  def change do
    alter table(:tasks) do
      add :lock_version, :integer, null: false, default: 1
    end
  end
end
