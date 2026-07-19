defmodule Taskman.Repo.Migrations.CreateTasks do
  use Ecto.Migration

  def change do
    create table(:tasks) do
      add :project_id, references(:projects, on_delete: :delete_all), null: false
      add :title, :string, null: false
      add :description, :text
      add :status, :string, null: false, default: "pending"
      add :priority, :string, null: false, default: "none"
      add :due_at, :naive_datetime

      timestamps(type: :utc_datetime)
    end

    create index(:tasks, [:project_id])

    create constraint(:tasks, :tasks_status_check,
             check:
               "status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'will_not_do')"
           )

    create constraint(:tasks, :tasks_priority_check,
             check: "priority IN ('none', 'low', 'medium', 'high', 'urgent')"
           )
  end
end
