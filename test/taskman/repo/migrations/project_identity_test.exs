defmodule Taskman.Repo.Migrations.ProjectIdentityTest do
  use Taskman.DataCase, async: false

  test "migration backfills existing Projects and reverses cleanly in an isolated schema" do
    schema = "project_identity_#{System.unique_integer([:positive])}"

    migration_path =
      Path.expand(
        "../../../../priv/repo/migrations/20260925113953_add_project_identity.exs",
        __DIR__
      )

    Code.require_file(migration_path)
    migration = Taskman.Repo.Migrations.AddProjectIdentity
    options = [prefix: schema, migration_lock: false, log: false]
    version = 20_260_925_113_953

    try do
      Ecto.Adapters.SQL.query!(Repo, ~s(CREATE SCHEMA "#{schema}"))

      Ecto.Adapters.SQL.query!(
        Repo,
        """
        CREATE TABLE "#{schema}".projects (
          id bigserial PRIMARY KEY,
          name varchar(255) NOT NULL,
          inserted_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        )
        """
      )

      Ecto.Adapters.SQL.query!(
        Repo,
        """
        INSERT INTO "#{schema}".projects (name, inserted_at, updated_at)
        VALUES ('Before migration', NOW(), NOW())
        """
      )

      assert :ok = Ecto.Migrator.up(Repo, version, migration, options)

      assert %{rows: [["Before migration", "", "briefcase", "#6366F1"]]} =
               Ecto.Adapters.SQL.query!(
                 Repo,
                 ~s(SELECT name, description, icon, color FROM "#{schema}".projects)
               )

      assert :ok = Ecto.Migrator.down(Repo, version, migration, options)

      assert %{rows: [["Before migration"]]} =
               Ecto.Adapters.SQL.query!(Repo, ~s(SELECT name FROM "#{schema}".projects))

      assert %{rows: []} =
               Ecto.Adapters.SQL.query!(
                 Repo,
                 """
                 SELECT column_name FROM information_schema.columns
                 WHERE table_schema = $1 AND table_name = 'projects'
                   AND column_name IN ('description', 'icon', 'color')
                 """,
                 [schema]
               )

      assert :ok = Ecto.Migrator.up(Repo, version, migration, options)

      assert %{rows: [["", "briefcase", "#6366F1"]]} =
               Ecto.Adapters.SQL.query!(
                 Repo,
                 ~s(SELECT description, icon, color FROM "#{schema}".projects)
               )
    after
      Ecto.Adapters.SQL.query!(Repo, ~s(DROP SCHEMA IF EXISTS "#{schema}" CASCADE))
    end
  end

  test "Project identity columns have non-null database defaults" do
    assert %{rows: rows} =
             Ecto.Adapters.SQL.query!(
               Repo,
               """
               SELECT column_name, is_nullable, column_default
               FROM information_schema.columns
               WHERE table_schema = current_schema()
                 AND table_name = 'projects'
                 AND column_name IN ('description', 'icon', 'color')
               ORDER BY column_name
               """
             )

    assert rows == [
             ["color", "NO", "'#6366F1'::character varying"],
             ["description", "NO", "''::text"],
             ["icon", "NO", "'briefcase'::character varying"]
           ]
  end

  test "database defaults initialize a Project inserted without identity fields" do
    assert %{rows: [[description, icon, color]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               """
               INSERT INTO projects (name, inserted_at, updated_at)
               VALUES ('Raw Project', NOW(), NOW())
               RETURNING description, icon, color
               """
             )

    assert {description, icon, color} == {"", "briefcase", "#6366F1"}
  end

  test "database rejects null Project identity fields" do
    for field <- ~w(description icon color) do
      error =
        assert_raise Postgrex.Error, fn ->
          Repo.transaction(
            fn ->
              Ecto.Adapters.SQL.query!(
                Repo,
                """
                INSERT INTO projects (name, #{field}, inserted_at, updated_at)
                VALUES ('Invalid Project', NULL, NOW(), NOW())
                """
              )
            end,
            mode: :savepoint
          )
        end

      assert error.postgres.code == :not_null_violation
    end

    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end
end
