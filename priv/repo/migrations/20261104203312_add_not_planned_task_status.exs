defmodule Taskman.Repo.Migrations.AddNotPlannedTaskStatus do
  use Ecto.Migration

  def up do
    drop constraint(:tasks, :tasks_status_check)

    create constraint(:tasks, :tasks_status_check,
             check:
               "status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'not_planned', 'will_not_do')"
           )
  end

  def down do
    drop constraint(:tasks, :tasks_status_check)

    create constraint(:tasks, :tasks_status_check,
             check:
               "status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'will_not_do')"
           )
  end
end
