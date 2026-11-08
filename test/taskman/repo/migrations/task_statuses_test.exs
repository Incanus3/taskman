defmodule Taskman.Repo.Migrations.TaskStatusesTest do
  use ExUnit.Case, async: false

  alias Ecto.Adapters.SQL
  alias Taskman.Repo
  alias Taskman.Repo.Migrations

  test "migration preserves rows, accepts Not Planned, and refuses down until explicitly reclassified" do
    SQL.Sandbox.unboxed_run(Repo, fn ->
      schema = "task_statuses_#{System.unique_integer([:positive])}"

      migration_path =
        Path.expand(
          "../../../../priv/repo/migrations/20261104203312_add_not_planned_task_status.exs",
          __DIR__
        )

      unless Code.ensure_loaded?(Migrations.AddNotPlannedTaskStatus),
        do: Code.require_file(migration_path)

      migration = Migrations.AddNotPlannedTaskStatus
      options = [prefix: schema, migration_lock: false, log: false]
      version = 20_261_104_203_312

      try do
        SQL.query!(Repo, ~s|CREATE SCHEMA "#{schema}"|)

        SQL.query!(Repo, """
        CREATE TABLE "#{schema}".tasks (
          id bigserial PRIMARY KEY,
          status text NOT NULL DEFAULT 'pending',
          CONSTRAINT tasks_status_check CHECK
            (status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'will_not_do'))
        )
        """)

        SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('pending'), ('will_not_do')|
        )

        assert :ok = Ecto.Migrator.up(Repo, version, migration, options)

        SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
        )

        assert %{rows: [["pending"], ["will_not_do"], ["not_planned"]]} =
                 SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        error =
          assert_raise Postgrex.Error, fn ->
            Ecto.Migrator.down(Repo, version, migration, options)
          end

        assert error.postgres.code == :check_violation

        assert %{rows: [["pending"], ["will_not_do"], ["not_planned"]]} =
                 SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        assert :already_up = Ecto.Migrator.up(Repo, version, migration, options)

        # Refused rollback leaves the seven-state constraint usable.
        SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
        )

        SQL.query!(
          Repo,
          ~s|UPDATE "#{schema}".tasks SET status = 'pending' WHERE status = 'not_planned'|
        )

        assert :ok = Ecto.Migrator.down(Repo, version, migration, options)

        assert %{rows: [["pending"], ["will_not_do"], ["pending"], ["pending"]]} =
                 SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        error =
          assert_raise Postgrex.Error, fn ->
            SQL.query!(
              Repo,
              ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
            )
          end

        assert error.postgres.code == :check_violation
        assert :ok = Ecto.Migrator.up(Repo, version, migration, options)
      after
        SQL.query!(Repo, ~s|DROP SCHEMA IF EXISTS "#{schema}" CASCADE|)
      end
    end)
  end
end
