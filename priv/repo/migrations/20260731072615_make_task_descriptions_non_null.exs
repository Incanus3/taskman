defmodule Taskman.Repo.Migrations.MakeTaskDescriptionsNonNull do
  use Ecto.Migration

  def up do
    execute("UPDATE tasks SET description = '' WHERE description IS NULL")

    alter table(:tasks) do
      modify :description, :text, null: false, default: ""
    end
  end

  def down do
    alter table(:tasks) do
      modify :description, :text, null: true, default: nil
    end
  end
end
