defmodule Taskman.Repo.Migrations.TaskCommentsTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks.Comment

  test "comment creation time has UTC second precision in storage and schema" do
    assert Comment.__schema__(:type, :created_at) == :utc_datetime

    assert %{rows: [[0]]} =
             Ecto.Adapters.SQL.query!(Repo, """
             SELECT datetime_precision
             FROM information_schema.columns
             WHERE table_name = 'task_comments' AND column_name = 'created_at'
             """)

    task = task_fixture(project_fixture(%{}))
    comment_id = insert_comment(task.id)

    assert %{rows: [[%NaiveDateTime{microsecond: {0, 0}}]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT created_at FROM task_comments WHERE id = $1",
               [
                 comment_id
               ]
             )
  end

  test "deleting a Task removes its comments" do
    task = task_fixture(project_fixture(%{}))
    comment_id = insert_comment(task.id)

    Ecto.Adapters.SQL.query!(Repo, "DELETE FROM tasks WHERE id = $1", [task.id])

    assert %{rows: []} =
             Ecto.Adapters.SQL.query!(Repo, "SELECT id FROM task_comments WHERE id = $1", [
               comment_id
             ])
  end

  test "deleting an account retains its comment and clears its actor" do
    task = task_fixture(project_fixture(%{}))
    user = user_fixture()
    comment_id = insert_comment(task.id, user.id)

    Ecto.Adapters.SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [Ecto.UUID.dump!(user.id)])

    assert %{rows: [[nil, "Stored note"]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT actor_user_id, text FROM task_comments WHERE id = $1",
               [comment_id]
             )
  end

  test "the database rejects empty text and nonpositive comment IDs" do
    task = task_fixture(project_fixture(%{}))

    for {query, params, constraint} <- [
          {"INSERT INTO task_comments (task_id, text, created_at) VALUES ($1, '', now())",
           [task.id], "task_comments_text_nonempty_check"},
          {"INSERT INTO task_comments (id, task_id, text, created_at) VALUES (0, $1, 'note', now())",
           [task.id], "task_comments_id_positive_check"}
        ] do
      error =
        assert_raise Postgrex.Error, fn ->
          Repo.transaction(fn -> Ecto.Adapters.SQL.query!(Repo, query, params) end,
            mode: :savepoint
          )
        end

      assert error.postgres.code == :check_violation
      assert error.postgres.constraint == constraint
    end
  end

  test "a Task update cannot move updated_at backwards" do
    task = task_fixture(project_fixture(%{}))
    later = ~N[2030-09-26 12:00:00]
    earlier = ~N[2029-09-25 12:00:00]

    Ecto.Adapters.SQL.query!(Repo, "UPDATE tasks SET updated_at = $1 WHERE id = $2", [
      later,
      task.id
    ])

    Ecto.Adapters.SQL.query!(Repo, "UPDATE tasks SET updated_at = $1 WHERE id = $2", [
      earlier,
      task.id
    ])

    assert %{rows: [[^later]]} =
             Ecto.Adapters.SQL.query!(Repo, "SELECT updated_at FROM tasks WHERE id = $1", [
               task.id
             ])
  end

  defp insert_comment(task_id, actor_user_id \\ nil) do
    %{rows: [[id]]} =
      Ecto.Adapters.SQL.query!(
        Repo,
        "INSERT INTO task_comments (task_id, actor_user_id, text, created_at) VALUES ($1, $2, 'Stored note', now()) RETURNING id",
        [task_id, actor_user_id && Ecto.UUID.dump!(actor_user_id)]
      )

    id
  end
end
