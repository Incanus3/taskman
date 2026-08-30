defmodule Taskman.Repo.Migrations.AddParentTaskHierarchy do
  use Ecto.Migration

  def change do
    create unique_index(:tasks, [:id, :project_id], name: :tasks_id_project_id_index)

    alter table(:tasks) do
      add :parent_task_id, :bigint
    end

    create constraint(:tasks, :tasks_parent_not_self_check,
             check: "parent_task_id IS NULL OR parent_task_id <> id"
           )

    alter table(:tasks) do
      modify :parent_task_id,
             references(:tasks,
               with: [project_id: :project_id],
               on_delete: :nothing,
               name: :tasks_parent_task_id_project_id_fkey
             ),
             from: :bigint
    end

    create index(:tasks, [:project_id, :parent_task_id],
             name: :tasks_project_id_parent_task_id_index
           )
  end
end
