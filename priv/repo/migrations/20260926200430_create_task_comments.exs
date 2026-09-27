defmodule Taskman.Repo.Migrations.CreateTaskComments do
  use Ecto.Migration

  def change do
    create table(:task_comments) do
      add :task_id, references(:tasks, on_delete: :delete_all), null: false
      add :actor_user_id, references(:users, type: :uuid, on_delete: :nilify_all)
      add :author_name, :text
      add :text, :text, null: false

      add :created_at, :utc_datetime,
        null: false,
        default: fragment("(now() AT TIME ZONE 'utc')")
    end

    create index(:task_comments, [:task_id, :created_at, :id])
    create constraint(:task_comments, :task_comments_id_positive_check, check: "id > 0")

    create constraint(:task_comments, :task_comments_text_nonempty_check,
             check: "length(text) > 0"
           )

    execute(
      """
      CREATE FUNCTION taskman_tasks_preserve_updated_at() RETURNS trigger AS $$
      BEGIN
        NEW.updated_at := GREATEST(OLD.updated_at, NEW.updated_at);
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql
      """,
      "DROP FUNCTION taskman_tasks_preserve_updated_at()"
    )

    execute(
      """
      CREATE TRIGGER taskman_tasks_preserve_updated_at
      BEFORE UPDATE ON tasks
      FOR EACH ROW EXECUTE FUNCTION taskman_tasks_preserve_updated_at()
      """,
      "DROP TRIGGER taskman_tasks_preserve_updated_at ON tasks"
    )
  end
end
