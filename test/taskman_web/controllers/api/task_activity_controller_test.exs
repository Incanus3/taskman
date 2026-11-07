defmodule TaskmanWeb.API.TaskActivityControllerTest do
  use TaskmanWeb.ConnCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Taskman.Repo
  alias Taskman.Tasks
  alias Taskman.Tasks.Activity.Event

  setup %{conn: conn} do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: key}} =
             Accounts.create_api_key(
               user,
               %{name: "Activity tests", expires_at: DateTime.add(now, 86_400, :second)},
               now: now
             )

    {:ok, conn: put_api_key(conn, key), user: user}
  end

  test "events are the default and optional comments retain timeline order and representation", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Original"}, actor: user)
    created = Repo.get_by!(Event, task_id: task.id, kind: "task_created")
    stamp = ~U[2026-01-01 10:00:00.000000Z]

    created
    |> Ecto.Changeset.change(first_changed_at: stamp, last_changed_at: stamp)
    |> Repo.update!()

    assert {:ok, comment} = Tasks.create_comment(project, task, user, %{text: "Note"})
    comment |> Ecto.Changeset.change(created_at: ~U[2026-01-01 10:00:01Z]) |> Repo.update!()
    assert {:ok, _} = Tasks.update_task(project, task, %{title: "Revised"}, actor: user)

    events = activity(conn, project, task)
    assert Enum.map(events, & &1["kind"]) == ["change", "change"]
    assert activity(conn, project, task, "?include_comments=false") == events

    [creation, mixed_comment, edit] = activity(conn, project, task, "?include_comments=true")
    assert [creation, edit] == events
    assert mixed_comment["id"] == "comment-#{comment.id}"
    assert mixed_comment["kind"] == "comment"
    assert mixed_comment["occurred_at"] == "2026-01-01T10:00:01Z"
    assert Map.keys(mixed_comment) |> Enum.sort() == ~w(comment id kind occurred_at)

    assert %{"data" => [ordinary_comment]} =
             conn |> get(task_path(project, task) <> "/comments") |> json_response(200)

    assert mixed_comment["comment"] == ordinary_comment

    assert %{"data" => shown} =
             conn
             |> get(task_path(project, task) <> "?include_comments=true")
             |> json_response(200)

    assert shown["comments"] == [ordinary_comment]

    assert %{"error" => %{"code" => "invalid_request"}} =
             conn
             |> get(task_path(project, task) <> "?include_comments=false")
             |> json_response(400)
  end

  test "unknown, malformed, nested and array-valued options are invalid", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    for query <- [
          "other=1",
          "include_comments=TRUE",
          "include_comments=1",
          "include_comments=",
          "include_comments=null",
          "include_comments[]=true",
          "include_comments[value]=true",
          "include_comments=true&other=1"
        ] do
      assert %{"error" => %{"code" => "invalid_request"}} =
               conn |> get(activity_path(project, task) <> "?" <> query) |> json_response(400)
    end
  end

  test "invalid UTF-8 query encoding uses the invalid_request envelope", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    {400, _headers, body} =
      assert_error_sent 400, fn ->
        conn
        |> put_req_header("accept", "application/json")
        |> get(activity_path(project, task) <> "?include_comments=%FF")
      end

    assert Jason.decode!(body) == %{
             "error" => %{"code" => "invalid_request", "message" => "Invalid request"}
           }
  end

  test "unauthenticated malformed queries are rejected before query decoding", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project)

    assert %{"error" => %{"code" => "unauthorized", "message" => "Authentication required"}} ==
             conn
             |> delete_req_header("authorization")
             |> get(activity_path(project, task) <> "?include_comments=%FF")
             |> json_response(401)
  end

  test "IDs follow existing validation and Project ownership rules", %{conn: conn} do
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
      assert %{"error" => %{"code" => ^code}} =
               conn
               |> get("/api/v1/projects/#{project_id}/tasks/#{task_id}/activity")
               |> json_response(status)
    end
  end

  test "activity requires an active authenticated account", %{conn: conn, user: user} do
    project = project_fixture(%{})
    task = task_fixture(project)

    assert %{"error" => %{"code" => "unauthorized"}} =
             conn
             |> delete_req_header("authorization")
             |> get(activity_path(project, task))
             |> json_response(401)

    user |> Ecto.Changeset.change(status: :disabled) |> Repo.update!()

    assert %{"error" => %{"code" => "unauthorized"}} =
             conn |> get(activity_path(project, task)) |> json_response(401)
  end

  test "API creation uses the authenticated actor and server time despite body overrides", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{name: "Example Project"})
    impostor = user_fixture()
    before_request = DateTime.utc_now()

    assert %{"data" => %{"id" => id}} =
             conn
             |> post("/api/v1/projects/#{project.id}/tasks", %{
               "task" => %{
                 "title" => "Created",
                 "actor_user_id" => impostor.id,
                 "created_at" => "2000-01-01T00:00:00Z",
                 "first_changed_at" => "2000-01-01T00:00:00Z",
                 "last_changed_at" => "2000-01-01T00:00:00Z"
               }
             })
             |> json_response(201)

    task = Tasks.get_task(id)
    [entry] = activity(conn, project, task)
    assert Map.keys(entry) |> Enum.sort() == ~w(change id kind occurred_at)
    assert entry["id"] =~ ~r/^event-\d+$/
    assert entry["kind"] == "change"
    change = entry["change"]

    assert Map.keys(change) |> Enum.sort() ==
             ~w(actor after before context field first_changed_at last_changed_at task_id type)

    assert change["type"] == "task_created"
    assert change["task_id"] == id
    assert change["field"] == nil
    assert change["before"] == nil
    assert change["after"] == nil
    assert change["actor"] == %{"user_id" => user.id, "login" => to_string(user.email)}

    assert change["context"] == %{
             "task" => %{
               "id" => id,
               "project_id" => project.id,
               "project_name" => "Example Project",
               "title" => "Created"
             }
           }

    assert entry["occurred_at"] == change["last_changed_at"]
    assert change["first_changed_at"] == change["last_changed_at"]
    assert {:ok, occurred_at, 0} = DateTime.from_iso8601(entry["occurred_at"])
    assert DateTime.compare(occurred_at, before_request) in [:eq, :gt]
    assert DateTime.compare(occurred_at, DateTime.utc_now()) in [:eq, :lt]
  end

  test "field values are canonical and update bodies cannot override actor or time", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{})

    task =
      task_fixture(project, %{title: "Before", status: :pending, priority: :low}, actor: user)

    impostor = user_fixture()
    before_request = DateTime.utc_now()

    assert %{"data" => _} =
             conn
             |> patch(task_path(project, task), %{
               "task" => %{
                 "title" => "After",
                 "status" => "in_progress",
                 "priority" => "high",
                 "due_at" => "2026-12-10T12:30:00",
                 "description" => "Private description",
                 "actor_user_id" => impostor.id,
                 "last_changed_at" => "2000-01-01T00:00:00Z"
               }
             })
             |> json_response(200)

    changes =
      activity(conn, project, task)
      |> Enum.map(& &1["change"])
      |> Enum.filter(&(&1["type"] == "field_changed"))

    assert Enum.map(changes, &{&1["field"], &1["before"], &1["after"]}) |> Enum.sort() == [
             {"due_at", nil, "2026-12-10T12:30:00"},
             {"priority", "low", "high"},
             {"status", "pending", "in_progress"},
             {"title", "Before", "After"}
           ]

    for change <- changes do
      assert change["actor"] == %{"user_id" => user.id, "login" => to_string(user.email)}
      assert {:ok, occurred_at, 0} = DateTime.from_iso8601(change["last_changed_at"])
      assert DateTime.compare(occurred_at, before_request) in [:eq, :gt]
    end
  end

  test "parent changes share historical public references on child and both parents", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{name: "Relationships"})
    original = task_fixture(project, %{title: "Original parent"})
    final = task_fixture(project, %{title: "Final parent"})
    child = task_fixture(project, %{title: "Child"}, parent: original, actor: user)
    assert {:ok, _} = Tasks.update_task(project, child, %{}, parent: final, actor: user)
    [entry] = field_entries(conn, project, child, "parent_task_id")
    assert field_entries(conn, project, original, "parent_task_id") == [entry]
    assert field_entries(conn, project, final, "parent_task_id") == [entry]

    assert entry["change"]["before"] == %{
             "id" => original.id,
             "project_id" => project.id,
             "project_name" => "Relationships",
             "title" => "Original parent"
           }

    assert entry["change"]["after"] == %{
             "id" => final.id,
             "project_id" => project.id,
             "project_name" => "Relationships",
             "title" => "Final parent"
           }

    assert entry["change"]["context"] == %{
             "task" => %{
               "id" => child.id,
               "project_id" => project.id,
               "project_name" => "Relationships",
               "title" => "Child"
             }
           }

    original |> Ecto.Changeset.change(title: "Renamed") |> Repo.update!()
    assert field_entries(conn, project, child, "parent_task_id") == [entry]
    Repo.delete!(original)
    assert field_entries(conn, project, child, "parent_task_id") == [entry]
  end

  test "List transitions retain project-level and nested location snapshots", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{name: "Locations"})
    outer = list_fixture(project, %{name: "Outer"})
    inner = list_fixture(project, outer, %{name: "Inner"})
    task = task_fixture(project, %{title: "Moved"}, actor: user)
    assert {:ok, _} = Tasks.move_task(project, task, inner, actor: user)
    [entry] = field_entries(conn, project, task, "list_id")

    assert entry["change"]["before"] == %{
             "project_id" => project.id,
             "project_name" => "Locations",
             "list_id" => nil,
             "path" => []
           }

    assert entry["change"]["after"] == %{
             "project_id" => project.id,
             "project_name" => "Locations",
             "list_id" => inner.id,
             "path" => ["Outer", "Inner"]
           }

    inner |> Ecto.Changeset.change(name: "Renamed") |> Repo.update!()
    assert field_entries(conn, project, task, "list_id") == [entry]
  end

  test "blocking changes share booleans and both endpoint snapshots across Projects", %{
    conn: conn,
    user: user
  } do
    project = project_fixture(%{name: "Source"})
    other = project_fixture(%{name: "Destination"})
    blocker = task_fixture(project, %{title: "Blocker"})
    blocked = task_fixture(other, %{title: "Blocked"})

    assert %{"data" => _} =
             conn
             |> post(task_path(project, blocker) <> "/blocks/#{blocked.id}", %{})
             |> json_response(201)

    [entry] = block_entries(conn, project, blocker)
    assert block_entries(conn, other, blocked) == [entry]
    change = entry["change"]
    assert change["type"] == "block_changed"
    assert change["task_id"] == blocker.id
    assert change["field"] == nil
    assert change["before"] == false
    assert change["after"] == true
    assert change["actor"] == %{"user_id" => user.id, "login" => to_string(user.email)}

    source = %{
      "id" => blocker.id,
      "project_id" => project.id,
      "project_name" => "Source",
      "title" => "Blocker"
    }

    target = %{
      "id" => blocked.id,
      "project_id" => other.id,
      "project_name" => "Destination",
      "title" => "Blocked"
    }

    assert change["context"] == %{
             "task" => source,
             "blocking_task" => source,
             "blocked_task" => target
           }

    assert {:ok, _} = Tasks.remove_block(project, blocker, blocked, actor: user_fixture())
    [^entry, removed] = block_entries(conn, other, blocked)
    assert removed["change"]["before"] == true
    assert removed["change"]["after"] == false
  end

  test "actors resolve current login and deleted users without exposing account data", %{
    conn: conn
  } do
    project = project_fixture(%{})
    actor = user_fixture()
    task = task_fixture(project, %{}, actor: actor)
    actor = actor |> Ecto.Changeset.change(email: "renamed@example.test") |> Repo.update!()
    [entry] = activity(conn, project, task)
    assert entry["change"]["actor"] == %{"user_id" => actor.id, "login" => "renamed@example.test"}
    Repo.delete!(actor)
    [entry] = activity(conn, project, task)
    assert entry["change"]["actor"] == %{"user_id" => nil, "login" => "Deleted user"}
  end

  defp activity(conn, project, task, query \\ "") do
    %{"data" => entries} =
      conn |> get(activity_path(project, task) <> query) |> json_response(200)

    entries
  end

  defp field_entries(conn, project, task, field),
    do: activity(conn, project, task) |> Enum.filter(&(&1["change"]["field"] == field))

  defp block_entries(conn, project, task),
    do: activity(conn, project, task) |> Enum.filter(&(&1["change"]["type"] == "block_changed"))

  defp activity_path(project, task), do: task_path(project, task) <> "/activity"
  defp task_path(project, task), do: "/api/v1/projects/#{project.id}/tasks/#{task.id}"
end
