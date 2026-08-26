defmodule Taskman.Repo.Migrations.AddListsAndTaskLocations do
  use Ecto.Migration

  def change do
    create table(:lists) do
      add :project_id, references(:projects, on_delete: :delete_all), null: false
      add :parent_list_id, :bigint
      add :name, :string, null: false

      timestamps(type: :utc_datetime)
    end

    create unique_index(:lists, [:id, :project_id], name: :lists_id_project_id_index)

    create unique_index(
             :lists,
             [:project_id, :parent_list_id, "lower(name)"],
             name: :lists_sibling_name_index,
             nulls_distinct: false
           )

    alter table(:lists) do
      modify :parent_list_id,
             references(:lists,
               with: [project_id: :project_id],
               on_delete: :nothing,
               name: :lists_parent_list_id_project_id_fkey
             ),
             from: :bigint
    end

    alter table(:tasks) do
      add :list_id,
          references(:lists,
            with: [project_id: :project_id],
            on_delete: :nothing,
            name: :tasks_list_id_project_id_fkey
          )
    end

    create index(:tasks, [:project_id, :list_id])
  end
end
