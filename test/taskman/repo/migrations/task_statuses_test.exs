defmodule Taskman.Repo.Migrations.TaskStatusesTest do
  use ExUnit.Case, async: false

  alias Taskman.Repo

  test "migration preserves rows, accepts Not Planned, and refuses down until explicitly reclassified" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      schema = "task_statuses_#{System.unique_integer([:positive])}"

      migration_path =
        Path.expand(
          "../../../../priv/repo/migrations/20261104203312_add_not_planned_task_status.exs",
          __DIR__
        )

      unless Code.ensure_loaded?(Taskman.Repo.Migrations.AddNotPlannedTaskStatus),
        do: Code.require_file(migration_path)

      migration = Taskman.Repo.Migrations.AddNotPlannedTaskStatus
      options = [prefix: schema, migration_lock: false, log: false]
      version = 20_261_104_203_312

      try do
        Ecto.Adapters.SQL.query!(Repo, ~s|CREATE SCHEMA "#{schema}"|)

        Ecto.Adapters.SQL.query!(Repo, """
        CREATE TABLE "#{schema}".tasks (
          id bigserial PRIMARY KEY,
          status text NOT NULL DEFAULT 'pending',
          CONSTRAINT tasks_status_check CHECK
            (status IN ('icebox', 'pending', 'in_progress', 'in_review', 'done', 'will_not_do'))
        )
        """)

        Ecto.Adapters.SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('pending'), ('will_not_do')|
        )

        assert :ok = Ecto.Migrator.up(Repo, version, migration, options)

        Ecto.Adapters.SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
        )

        assert %{rows: [["pending"], ["will_not_do"], ["not_planned"]]} =
                 Ecto.Adapters.SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        error =
          assert_raise Postgrex.Error, fn ->
            Ecto.Migrator.down(Repo, version, migration, options)
          end

        assert error.postgres.code == :check_violation

        assert %{rows: [["pending"], ["will_not_do"], ["not_planned"]]} =
                 Ecto.Adapters.SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        assert :already_up = Ecto.Migrator.up(Repo, version, migration, options)

        # Refused rollback leaves the seven-state constraint usable.
        Ecto.Adapters.SQL.query!(
          Repo,
          ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
        )

        Ecto.Adapters.SQL.query!(
          Repo,
          ~s|UPDATE "#{schema}".tasks SET status = 'pending' WHERE status = 'not_planned'|
        )

        assert :ok = Ecto.Migrator.down(Repo, version, migration, options)

        assert %{rows: [["pending"], ["will_not_do"], ["pending"], ["pending"]]} =
                 Ecto.Adapters.SQL.query!(
                   Repo,
                   ~s|SELECT status FROM "#{schema}".tasks ORDER BY id|
                 )

        error =
          assert_raise Postgrex.Error, fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              ~s|INSERT INTO "#{schema}".tasks (status) VALUES ('not_planned')|
            )
          end

        assert error.postgres.code == :check_violation
        assert :ok = Ecto.Migrator.up(Repo, version, migration, options)
      after
        Ecto.Adapters.SQL.query!(Repo, ~s|DROP SCHEMA IF EXISTS "#{schema}" CASCADE|)
      end
    end)
  end
end
