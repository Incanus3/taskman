defmodule Taskman.CLI.Commands.CommentsTest do
  use ExUnit.Case, async: true

  setup {Req.Test, :verify_on_exit!}

  @comment %{
    id: 123,
    task_id: 42,
    author: %{display_name: "Research agent", login: "person@example.com"},
    text: "First line\nSecond line",
    created_at: "2026-09-26T12:34:56Z"
  }

  @task %{
    id: 42,
    project_id: 7,
    list_id: nil,
    parent_task_id: nil,
    title: "Review",
    description: "",
    status: "pending",
    priority: "none",
    due_at: nil,
    location: %{kind: "project", list_id: nil, path: []}
  }

  test "lists an ordered thread in readable and JSON modes" do
    later = %{@comment | id: 124, text: "Later note", created_at: "2026-09-26T12:35:00Z"}

    for json? <- [false, true] do
      Req.Test.expect(CommentsCommands, fn conn ->
        assert conn.method == "GET"
        assert conn.request_path == "/api/v1/projects/7/tasks/42/comments"
        assert conn.query_string == ""
        Req.Test.json(conn, %{data: [@comment, later]})
      end)

      args = ~w(tasks comments list --project 7 42) ++ if(json?, do: ["--json"], else: [])
      result = run(args)
      assert result.status == 0, result.stderr

      if json? do
        assert %{"data" => [%{"id" => 123, "text" => "First line\nSecond line"}, %{"id" => 124}]} =
                 Jason.decode!(result.stdout)
      else
        assert result.stdout =~ "Research agent (person@example.com)"
        assert result.stdout =~ "First line\nSecond line"
        assert result.stdout =~ ~r/#123.*First line\nSecond line.*#124.*Later note/s
      end
    end
  end

  test "adds only the comment envelope with optional display name" do
    Req.Test.expect(CommentsCommands, fn conn ->
      assert conn.method == "POST"
      assert conn.request_path == "/api/v1/projects/7/tasks/42/comments"

      assert Jason.decode!(Req.Test.raw_body(conn)) == %{
               "comment" => %{"text" => "Review notes", "author_name" => "Research agent"}
             }

      conn |> Plug.Conn.put_status(201) |> Req.Test.json(%{data: @comment})
    end)

    result =
      run([
        "tasks",
        "comments",
        "add",
        "--project",
        "7",
        "42",
        "--text",
        "Review notes",
        "--author-name",
        "Research agent",
        "--json"
      ])

    assert result.status == 0, result.stderr
    assert %{"data" => %{"id" => 123}} = Jason.decode!(result.stdout)
  end

  test "add renders the returned comment in readable mode" do
    Req.Test.expect(CommentsCommands, fn conn ->
      assert Jason.decode!(Req.Test.raw_body(conn)) == %{"comment" => %{"text" => "Review notes"}}
      conn |> Plug.Conn.put_status(201) |> Req.Test.json(%{data: @comment})
    end)

    result = run(["tasks", "comments", "add", "--project", "7", "42", "--text", "Review notes"])
    assert result.status == 0, result.stderr
    assert result.stdout =~ "#123"
    assert result.stdout =~ "Research agent (person@example.com)"
  end

  test "add omits author_name when not supplied and defers empty text validation to the API" do
    Req.Test.expect(CommentsCommands, fn conn ->
      assert Jason.decode!(Req.Test.raw_body(conn)) == %{"comment" => %{"text" => "  "}}

      conn
      |> Plug.Conn.put_status(422)
      |> Req.Test.json(%{
        error: %{
          code: "validation_failed",
          message: "invalid",
          fields: %{text: ["can't be blank"]}
        }
      })
    end)

    result = run(["tasks", "comments", "add", "--project", "7", "42", "--text", "  ", "--json"])
    assert result.status == 3
    assert Jason.decode!(result.stderr)["error"]["fields"]["text"] == ["can't be blank"]
  end

  test "connection failure from comment list exits 4" do
    Req.Test.expect(CommentsCommands, fn conn -> Req.Test.transport_error(conn, :econnrefused) end)

    result = run(~w(tasks comments list --project 7 42 --json))
    assert result.status == 4
    assert Jason.decode!(result.stderr)["error"]["code"] == "connection_failed"
  end

  test "show sends include_comments only when selected" do
    Req.Test.expect(CommentsCommands, fn conn ->
      assert conn.query_params == %{"include_comments" => "true"}
      Req.Test.json(conn, %{data: Map.put(@task, :comments, [@comment])})
    end)

    result = run(~w(tasks show --project 7 42 --include-comments))
    assert result.status == 0, result.stderr
    assert result.stdout =~ "COMMENTS"
    assert result.stdout =~ "First line\nSecond line"

    Req.Test.expect(CommentsCommands, fn conn ->
      assert conn.query_string == ""
      Req.Test.json(conn, %{data: @task})
    end)

    ordinary = run(~w(tasks show --project 7 42))
    assert ordinary.status == 0, ordinary.stderr
    refute ordinary.stdout =~ "COMMENTS"

    Req.Test.expect(CommentsCommands, fn conn ->
      assert conn.query_params == %{"include_comments" => "true"}
      Req.Test.json(conn, %{data: Map.put(@task, :comments, [@comment])})
    end)

    json_show = run(~w(tasks show --project 7 42 --include-comments --json))
    assert json_show.status == 0, json_show.stderr
    assert %{"data" => %{"comments" => [%{"id" => 123}]}} = Jason.decode!(json_show.stdout)
  end

  test "empty thread has a readable empty state" do
    Req.Test.expect(CommentsCommands, fn conn -> Req.Test.json(conn, %{data: []}) end)
    result = run(~w(tasks comments list --project 7 42))
    assert result.status == 0, result.stderr
    assert result.stdout =~ "No comments yet"
  end

  test "comment failures retain CLI status mapping" do
    for {http, code, status} <- [
          {400, "invalid_request", 3},
          {404, "not_found", 3},
          {422, "validation_failed", 3},
          {401, "unauthorized", 7},
          {403, "forbidden", 7},
          {500, "internal_error", 5}
        ] do
      Req.Test.expect(CommentsCommands, fn conn ->
        conn
        |> Plug.Conn.put_status(http)
        |> Req.Test.json(%{error: %{code: code, message: "failure"}})
      end)

      result = run(~w(tasks comments list --project 7 42 --json))
      assert result.status == status
      assert result.stdout == ""
      assert %{"error" => %{"code" => ^code}} = Jason.decode!(result.stderr)
    end

    assert run(~w(tasks comments add --project 7 42)).status == 2
    assert run(~w(tasks comments list --project 7 0)).status == 2

    assert Taskman.CLI.run(~w(tasks comments list --project 7 42),
             env: %{},
             config_root: Path.join(System.tmp_dir!(), "taskman-cli-comment-tests-missing-key")
           ).status == 7
  end

  test "malformed successful comment and opt-in show responses exit 5" do
    Req.Test.expect(CommentsCommands, fn conn ->
      Req.Test.json(conn, %{data: [%{@comment | created_at: "2026-09-26T13:34:56+01:00"}]})
    end)

    invalid_thread = run(~w(tasks comments list --project 7 42 --json))
    assert invalid_thread.status == 5
    assert Jason.decode!(invalid_thread.stderr)["error"]["code"] == "invalid_response"

    Req.Test.expect(CommentsCommands, fn conn ->
      Req.Test.json(conn, %{
        data: Map.put(@task, :comments, [%{@comment | author: %{login: "person@example.com"}}])
      })
    end)

    assert run(~w(tasks show --project 7 42 --include-comments)).status == 5
  end

  defp run(args) do
    Taskman.CLI.run(args,
      env: %{"TASKMAN_API_KEY" => "tm_comment_test_credential"},
      config_root: Path.join(System.tmp_dir!(), "taskman-cli-comment-tests"),
      req_options: [plug: {Req.Test, CommentsCommands}]
    )
  end
end
