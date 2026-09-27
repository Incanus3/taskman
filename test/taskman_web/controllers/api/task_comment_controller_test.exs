defmodule TaskmanWeb.API.TaskCommentControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Taskman.Repo
  alias Taskman.Tasks

  setup %{conn: conn} do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: key}} =
             Accounts.create_api_key(
               user,
               %{name: "Comment tests", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    {:ok, conn: put_api_key(conn, key), user: user, key: key}
  end

  test "GET returns an empty thread and then comments in creation order", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = comments_path(project, task)

    assert %{"data" => []} = conn |> get(path) |> json_response(200)

    assert {:ok, first} = Tasks.create_comment(project, task, user, %{"text" => "First"})
    assert {:ok, second} = Tasks.create_comment(project, task, user, %{"text" => "Second"})

    assert %{"data" => [%{"id" => first_id}, %{"id" => second_id}]} =
             conn |> get(path) |> json_response(200)

    assert first_id == first.id
    assert second_id == second.id
  end

  test "POST trims fields and returns the exact comment representation", %{conn: conn, user: user} do
    project = project_fixture(%{})
    task = task_fixture(project)

    response =
      conn
      |> post(comments_path(project, task), %{
        "comment" => %{"text" => "  Review notes  ", "author_name" => "  Research agent  "}
      })
      |> json_response(201)

    assert %{"data" => data} = response
    assert Map.keys(data) |> Enum.sort() == ~w(author created_at id task_id text)
    assert data["id"] > 0
    assert data["task_id"] == task.id
    assert data["text"] == "Review notes"

    assert data["author"] == %{
             "display_name" => "Research agent",
             "login" => to_string(user.email)
           }

    assert {:ok, created_at, 0} = DateTime.from_iso8601(data["created_at"])
    assert created_at.microsecond == {0, 0}
  end

  test "POST without author_name returns null display name and the API key owner's login", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})
    task = task_fixture(project)

    assert %{"data" => %{"author" => author}} =
             conn
             |> post(comments_path(project, task), %{"comment" => %{"text" => "Note"}})
             |> json_response(201)

    assert author == %{"display_name" => nil, "login" => to_string(user.email)}
    refute Map.has_key?(author, "id")
  end

  test "GET and POST reject malformed IDs, missing and cross-Project Tasks", %{conn: conn} do
    project = project_fixture(%{})
    foreign = project_fixture(%{})
    task = task_fixture(foreign)

    for {project_id, task_id, status, code} <- [
          {"bad", task.id, 400, "invalid_request"},
          {project.id, "0", 400, "invalid_request"},
          {"999999999", task.id, 404, "not_found"},
          {project.id, "999999999", 404, "not_found"},
          {project.id, task.id, 404, "not_found"}
        ] do
      path = "/api/v1/projects/#{project_id}/tasks/#{task_id}/comments"

      assert %{"error" => %{"code" => ^code}} =
               conn |> get(path) |> json_response(status)

      assert %{"error" => %{"code" => ^code}} =
               conn
               |> post(path, %{"comment" => %{"text" => "No insertion"}})
               |> json_response(status)
    end
  end

  test "GET and POST reject every query key", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = comments_path(project, task)

    for query <- ["page=1", "search=note", "include_comments=true"] do
      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> get(path <> "?" <> query) |> json_response(400)

      assert %{"error" => %{"code" => "invalid_request"}} =
               conn
               |> post(path <> "?" <> query, %{"comment" => %{"text" => "No insertion"}})
               |> json_response(400)
    end

    assert %{"data" => []} = conn |> get(path) |> json_response(200)
  end

  test "POST rejects malformed envelope, non-string fields, and account or response overrides", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = comments_path(project, task)

    for body <- [
          %{},
          %{"comment" => []},
          %{"comment" => "text"},
          %{"comment" => %{"text" => 12}},
          %{"comment" => %{"text" => "Note", "author_name" => nil}},
          %{"comment" => %{"text" => "Note"}, "other" => true},
          %{"comment" => %{"text" => "Note", "id" => 5}},
          %{"comment" => %{"text" => "Note", "task_id" => task.id}},
          %{"comment" => %{"text" => "Note", "actor_user_id" => Ecto.UUID.generate()}},
          %{"comment" => %{"text" => "Note", "author" => %{"login" => "fake"}}},
          %{"comment" => %{"text" => "Note", "created_at" => "2026-01-01"}}
        ] do
      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> post(path, body) |> json_response(400)
    end

    assert %{"data" => []} = conn |> get(path) |> json_response(200)
  end

  test "POST reports missing, blank, and over-limit text and author_name as field errors", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = comments_path(project, task)

    for {body, field} <- [
          {%{"comment" => %{}}, "text"},
          {%{"comment" => %{"text" => "  "}}, "text"},
          {%{"comment" => %{"text" => String.duplicate("a", 10_001)}}, "text"},
          {%{"comment" => %{"text" => "Note", "author_name" => "  "}}, "author_name"},
          {%{"comment" => %{"text" => "Note", "author_name" => String.duplicate("a", 81)}},
           "author_name"}
        ] do
      assert %{"error" => %{"code" => "validation_failed", "fields" => fields}} =
               conn |> post(path, body) |> json_response(422)

      assert [_ | _] = fields[field]
    end

    assert %{"data" => []} = conn |> get(path) |> json_response(200)
  end

  test "disabled or revoked API credentials cannot read or post comments", %{
    conn: conn,
    user: user,
    key: key
  } do
    project = project_fixture(%{})
    task = task_fixture(project)
    path = comments_path(project, task)

    now = DateTime.utc_now()

    assert {:ok, %{api_key: second_key, plaintext: revoked_key}} =
             Accounts.create_api_key(
               user,
               %{name: "Revoked comment key", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    assert :ok = Accounts.revoke_api_key(user, second_key.id)

    revoked_conn = put_api_key(recycle(conn), revoked_key)

    assert %{"error" => %{"code" => "unauthorized"}} =
             revoked_conn |> get(path) |> json_response(401)

    assert %{"error" => %{"code" => "unauthorized"}} =
             revoked_conn
             |> post(path, %{"comment" => %{"text" => "Denied"}})
             |> json_response(401)

    assert {:ok, _disabled} = user |> Ecto.Changeset.change(status: :disabled) |> Repo.update()

    disabled_conn = put_api_key(recycle(conn), key)

    assert %{"error" => %{"code" => "unauthorized"}} =
             disabled_conn |> get(path) |> json_response(401)

    assert %{"error" => %{"code" => "unauthorized"}} =
             disabled_conn
             |> post(path, %{"comment" => %{"text" => "Denied"}})
             |> json_response(401)
  end

  test "POST rejects a non-object top-level JSON value", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    response =
      conn
      |> put_req_header("content-type", "application/json")
      |> post(comments_path(project, task), Jason.encode!([%{"text" => "No insertion"}]))

    assert %{"error" => %{"code" => "invalid_request"}} = json_response(response, 400)
    assert %{"data" => []} = conn |> get(comments_path(project, task)) |> json_response(200)
  end

  defp comments_path(project, task),
    do: "/api/v1/projects/#{project.id}/tasks/#{task.id}/comments"
end
