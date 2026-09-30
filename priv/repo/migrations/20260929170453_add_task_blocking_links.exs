defmodule Taskman.Repo.Migrations.AddTaskBlockingLinks do
  use Ecto.Migration

  def change do
    create table(:task_blocking_links, primary_key: false) do
      add :id, :bigserial, primary_key: true

      add :blocking_task_id, references(:tasks, type: :bigint, on_delete: :delete_all),
        null: false

      add :blocked_task_id, references(:tasks, type: :bigint, on_delete: :delete_all), null: false

      timestamps(type: :utc_datetime)
    end

    create unique_index(:task_blocking_links, [:blocking_task_id, :blocked_task_id])
    create index(:task_blocking_links, [:blocking_task_id])
    create index(:task_blocking_links, [:blocked_task_id])

    create constraint(:task_blocking_links, :task_blocking_links_not_self_check,
             check: "blocking_task_id <> blocked_task_id"
           )
  end
end
