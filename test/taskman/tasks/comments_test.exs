defmodule Taskman.Tasks.CommentsTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Taskman.Accounts.User.Persistence, as: UserPersistence
  alias Taskman.Tasks
  alias Taskman.Tasks.Comment

  test "an empty Task thread is explicit and Project scoped" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project)

    assert {:ok, []} = Tasks.list_comments(project, task)
    assert {:error, :not_found} = Tasks.list_comments(other_project, task)
  end

  test "a Task thread contains only its comments in timestamp then ID order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project)
    other_task = task_fixture(other_project)
    user = user_fixture()
    first_time = ~U[2026-09-25 10:00:00Z]
    next_time = ~U[2026-09-25 11:00:00Z]
    newer = insert_comment(task.id, user.id, "Newer", next_time)
    first = insert_comment(task.id, user.id, "First", first_time)
    same_time = insert_comment(task.id, user.id, "Same time", first_time)
    _foreign = insert_comment(other_task.id, user.id, "Foreign", first_time)

    assert {:ok, comments} = Tasks.list_comments(project, task)
    assert Enum.map(comments, & &1.id) == [first, same_time, newer]
    assert Enum.map(comments, & &1.text) == ["First", "Same time", "Newer"]

    assert Enum.map(comments, & &1.author_login) ==
             [to_string(user.email), to_string(user.email), to_string(user.email)]
  end

  test "reads use the posting account's current email" do
    project = project_fixture(%{})
    task = task_fixture(project)
    user = user_fixture()
    _comment_id = insert_comment(task.id, user.id, "Before rename")

    assert {:ok, [%Comment{author_login: original}]} = Tasks.list_comments(project, task)
    assert original == to_string(user.email)

    assert {:ok, _updated} =
             UserPersistence.update_email(user, %{email: "current@example.com"})

    assert {:ok, [%Comment{author_login: "current@example.com"}]} =
             Tasks.list_comments(project, task)
  end

  test "deleted accounts display a fallback without losing custom names or text" do
    project = project_fixture(%{})
    task = task_fixture(project)
    user = user_fixture()
    _comment_id = insert_comment(task.id, user.id, "Retained", nil, "Research agent")

    Ecto.Adapters.SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [Ecto.UUID.dump!(user.id)])

    assert {:ok,
            [
              %Comment{
                author_login: "Deleted user",
                author_name: "Research agent",
                text: "Retained"
              }
            ]} =
             Tasks.list_comments(project, task)
  end

  test "emails_by_user_id batches current emails for requested account IDs" do
    first = user_fixture()
    second = user_fixture()

    assert Accounts.emails_by_user_id([first.id, second.id, Ecto.UUID.generate()]) == %{
             first.id => to_string(first.email),
             second.id => to_string(second.email)
           }
  end

  test "reading a thread resolves its authors in one account query" do
    project = project_fixture(%{})
    task = task_fixture(project)
    first = user_fixture()
    second = user_fixture()
    _first_comment = insert_comment(task.id, first.id, "First")
    _second_comment = insert_comment(task.id, second.id, "Second")
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query}, ^test_pid ->
          if self() == test_pid and is_binary(query) and
               String.contains?(query, ~s(FROM "users")) do
            send(test_pid, :user_email_query)
          end
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    assert {:ok, [_first, _second]} = Tasks.list_comments(project, task)
    assert_receive :user_email_query
    refute_receive :user_email_query

    agent = start_supervised!({Agent, fn -> :ready end})
    :ok = Ecto.Adapters.SQL.Sandbox.allow(Repo, self(), agent)

    assert Agent.get(agent, fn :ready -> Accounts.emails_by_user_id([first.id]) end) == %{
             first.id => to_string(first.email)
           }

    refute_receive :user_email_query
  end

  test "changeset trims edges, keeps internal newlines and markup-looking text" do
    changeset = Comment.changeset(struct(Comment), %{text: "  first\n<b>second</b>  "})

    assert changeset.valid?
    assert Ecto.Changeset.get_field(changeset, :text) == "first\n<b>second</b>"
    assert Ecto.Changeset.get_field(changeset, :author_name) == nil
  end

  test "changeset validates supplied custom name and text after trimming" do
    for attrs <- [
          %{text: nil},
          %{text: " \n "},
          %{text: "ok", author_name: "  "},
          %{text: "ok", author_name: nil},
          %{text: String.duplicate("a", 10_001)},
          %{text: "ok", author_name: String.duplicate("a", 81)}
        ] do
      refute Comment.changeset(struct(Comment), attrs).valid?
    end

    changeset =
      Comment.changeset(struct(Comment), %{text: " ok ", author_name: " Research agent "})

    assert changeset.valid?
    assert Ecto.Changeset.get_field(changeset, :author_name) == "Research agent"
  end

  test "length limits count Unicode grapheme clusters" do
    text = String.duplicate("e\u0301", 10_000)
    name = String.duplicate("👩🏽‍💻", 80)
    changeset = Comment.changeset(struct(Comment), %{text: text, author_name: name})

    assert changeset.valid?
    assert Ecto.Changeset.get_field(changeset, :text) == text
    assert Ecto.Changeset.get_field(changeset, :author_name) == name
    refute Comment.changeset(struct(Comment), %{text: text <> "x"}).valid?
    refute Comment.changeset(struct(Comment), %{text: "ok", author_name: name <> "x"}).valid?
  end

  test "caller cannot set Task, actor, ID, or creation time through changeset attrs" do
    changeset =
      Comment.changeset(struct(Comment), %{
        text: "ok",
        task_id: 999,
        actor_user_id: Ecto.UUID.generate(),
        id: 42,
        created_at: ~U[2030-01-01 00:00:00Z]
      })

    assert changeset.valid?
    assert Map.keys(changeset.changes) == [:text]
  end

  test "creation trims before validation and persists Task activity without editing its fields" do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :done})
    actor = user_fixture()

    assert {:ok, comment} =
             Tasks.create_comment(project, task, actor, %{
               text: "  Review\nnotes  ",
               author_name: "  Research agent  "
             })

    assert comment.text == "Review\nnotes"
    assert comment.author_name == "Research agent"
    assert comment.actor_user_id == actor.id
    assert comment.task_id == task.id
    assert comment.author_login == to_string(actor.email)
    assert {:ok, [listed]} = Tasks.list_comments(project, task)
    assert listed.id == comment.id

    persisted = Tasks.get_task_for_project(project, task.id)
    assert persisted.status == :done
    assert persisted.lock_version == task.lock_version
    assert persisted.updated_at == comment.created_at
  end

  test "validation rejects trimmed empty and overlong values without touching Task metadata" do
    project = project_fixture(%{})
    task = task_fixture(project)
    actor = user_fixture()

    for {attrs, field} <- [
          {%{text: " \n "}, :text},
          {%{text: " " <> String.duplicate("a", 10_001) <> " "}, :text},
          {%{text: "ok", author_name: "  "}, :author_name},
          {%{text: "ok", author_name: " " <> String.duplicate("a", 81) <> " "}, :author_name}
        ] do
      assert {:error, changeset} = Tasks.create_comment(project, task, actor, attrs)
      assert Map.has_key?(errors_on(changeset), field)
      assert Tasks.get_task_for_project(project, task.id).updated_at == task.updated_at
    end

    assert {:ok, []} = Tasks.list_comments(project, task)
  end

  test "creation checks current Task scope and active persisted actor" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{status: :will_not_do})
    actor = user_fixture()
    pending = pending_user_fixture()
    disabled = user_fixture(%{status: :disabled})
    stale_disabled = user_fixture()

    assert {:error, :not_found} =
             Tasks.create_comment(other_project, task, actor, %{text: "No scope"})

    assert {:error, :authentication_required} =
             Tasks.create_comment(project, task, pending, %{text: "Pending"})

    assert {:error, :authentication_required} =
             Tasks.create_comment(project, task, disabled, %{text: "Disabled"})

    Ecto.Adapters.SQL.query!(Repo, "UPDATE users SET status = 'disabled' WHERE id = $1", [
      Ecto.UUID.dump!(stale_disabled.id)
    ])

    assert {:error, :authentication_required} =
             Tasks.create_comment(project, task, stale_disabled, %{text: "Stale actor"})

    Ecto.Adapters.SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [Ecto.UUID.dump!(actor.id)])

    assert {:error, :authentication_required} =
             Tasks.create_comment(project, task, actor, %{text: "Deleted"})

    assert {:ok, []} = Tasks.list_comments(project, task)
    assert Tasks.get_task_for_project(project, task.id).updated_at == task.updated_at

    survivor = user_fixture()

    assert {:ok, _comment} =
             Tasks.create_comment(project, task, survivor, %{
               text: "Terminal status permits comments"
             })

    Repo.delete!(Tasks.get_task_for_project(project, task.id))

    assert {:error, :not_found} =
             Tasks.create_comment(project, task, survivor, %{text: "Deleted task"})
  end

  defp insert_comment(task_id, actor_user_id, text, at \\ nil, name \\ nil) do
    at = at || DateTime.utc_now()

    %{rows: [[id]]} =
      Ecto.Adapters.SQL.query!(
        Repo,
        "INSERT INTO task_comments (task_id, actor_user_id, author_name, text, created_at) VALUES ($1, $2, $3, $4, $5) RETURNING id",
        [task_id, Ecto.UUID.dump!(actor_user_id), name, text, at]
      )

    id
  end
end
