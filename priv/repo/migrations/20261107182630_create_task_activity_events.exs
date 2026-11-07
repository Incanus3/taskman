defmodule Taskman.Repo.Migrations.CreateTaskActivityEvents do
  use Ecto.Migration

  def change do
    create table(:task_activity_events) do
      add :task_id, references(:tasks, on_delete: :delete_all), null: false
      add :actor_user_id, references(:users, type: :uuid, on_delete: :nilify_all)
      add :grouping_key, :text, null: false
      add :kind, :text, null: false
      add :field, :text
      add :before_value, :map, null: false
      add :after_value, :map, null: false
      add :context, :map, null: false
      add :first_changed_at, :utc_datetime_usec, null: false
      add :last_changed_at, :utc_datetime_usec, null: false
    end

    create index(:task_activity_events, [:grouping_key, :last_changed_at, :id])
    create index(:task_activity_events, [:task_id])
    create index(:task_activity_events, [:actor_user_id])

    create constraint(:task_activity_events, :task_activity_events_id_positive_check,
             check: "id > 0"
           )

    create constraint(:task_activity_events, :task_activity_events_kind_field_check,
             check: """
             (kind IN ('task_created', 'block_changed') AND field IS NULL) OR
             (kind = 'field_changed' AND field IS NOT NULL AND
               field IN ('title', 'status', 'priority', 'due_at', 'list_id', 'parent_task_id'))
             """
           )

    create constraint(:task_activity_events, :task_activity_events_values_check,
             check: """
             jsonb_typeof(before_value) = 'object' AND before_value ? 'value' AND
             jsonb_typeof(after_value) = 'object' AND after_value ? 'value' AND
             (kind <> 'block_changed' OR
               (jsonb_typeof(before_value->'value') = 'boolean' AND
                jsonb_typeof(after_value->'value') = 'boolean')) AND
             (kind <> 'task_created' OR
               (before_value->'value' = 'null'::jsonb AND after_value->'value' = 'null'::jsonb))
             """
           )

    create constraint(:task_activity_events, :task_activity_events_context_check,
             check: """
             jsonb_typeof(context) = 'object' AND context ? 'task' AND
             jsonb_typeof(context->'task') = 'object' AND
             (kind <> 'block_changed' OR
               (context ? 'blocking_task' AND context ? 'blocked_task'))
             """
           )

    create constraint(:task_activity_events, :task_activity_events_time_check,
             check: "last_changed_at >= first_changed_at"
           )

    create constraint(:task_activity_events, :task_activity_events_grouping_key_check,
             check: "length(grouping_key) > 0"
           )

    create table(:task_activity_event_tasks, primary_key: false) do
      add :event_id, references(:task_activity_events, on_delete: :delete_all),
        null: false,
        primary_key: true

      add :task_id, references(:tasks, on_delete: :delete_all), null: false, primary_key: true
    end

    create index(:task_activity_event_tasks, [:task_id, :event_id])
  end
end
