defmodule Taskman.CLI.Commands.TaskBlockingTest do
  use ExUnit.Case, async: true

  @blocker %{
    id: 12,
    project_id: 7,
    project_name: "Launch",
    title: "Approve copy",
    status: "in_review",
    priority: "high",
    location: %{kind: "list", list_id: 3, path: ["Review"]}
  }
  @blocked %{
    id: 42,
    project_id: 9,
    project_name: "Website",
    title: "Publish site",
    status: "pending",
    priority: "urgent",
    location: %{kind: "project", list_id: nil, path: []}
  }

  test "blocking show reads both directions in the selected Task's Project" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      assert conn.method == "GET"
      assert conn.request_path == "/api/v1/projects/9/tasks/42/blocking"
      assert conn.query_string == ""
      Req.Test.json(conn, %{data: %{blocks: [], blocked_by: [@blocker]}})
    end)

    result = run(["tasks", "blocking", "show", "--project", "9", "42"])
    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout =~ "BLOCKS\nNone\n"
    assert result.stdout =~ "BLOCKED BY"

    for value <- ["12", "Approve copy", "in_review", "high", "7: Launch", "Review"] do
      assert result.stdout =~ value
    end
  end

  test "blocking show JSON preserves the API data envelope" do
    data = %{blocks: [@blocked], blocked_by: []}
    Req.Test.expect(TaskBlockingCommands, fn conn -> Req.Test.json(conn, %{data: data}) end)

    result = run(["tasks", "blocking", "show", "--project", "7", "12", "--json"])
    assert result.status == 0
    assert Jason.decode!(result.stdout) == %{"data" => Jason.decode!(Jason.encode!(data))}
  end

  test "blocks add and remove use the blocking Task's Project and never send a body" do
    for {action, method, status} <- [{"add", "POST", 201}, {"remove", "DELETE", 200}] do
      Req.Test.expect(TaskBlockingCommands, fn conn ->
        assert conn.method == method
        assert conn.request_path == "/api/v1/projects/7/tasks/12/blocks/42"
        assert Req.Test.raw_body(conn) == ""
        conn |> Plug.Conn.put_status(status) |> Req.Test.json(%{data: edge()})
      end)

      result = run(["tasks", "blocks", action, "--project", "7", "12", "--target", "42"])
      assert result.status == 0
      assert result.stderr == ""
      assert result.stdout =~ "Task 12 blocks Task 42 (Website)."
    end
  end

  test "blocks mutation JSON preserves named endpoints" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      conn |> Plug.Conn.put_status(201) |> Req.Test.json(%{data: edge()})
    end)

    result = run(["tasks", "blocks", "add", "--project", "7", "12", "--target", "42", "--json"])
    assert result.status == 0
    assert Jason.decode!(result.stdout) == %{"data" => Jason.decode!(Jason.encode!(edge()))}
  end

  test "relationship commands reject malformed success summaries" do
    bad_summary = Map.delete(@blocker, :priority)

    for {argv, data} <- [
          {["tasks", "blocking", "show", "--project", "9", "42"],
           %{blocks: [], blocked_by: [bad_summary]}},
          {["tasks", "blocks", "add", "--project", "7", "12", "--target", "42"],
           %{blocking_task: bad_summary, blocked_task: @blocked}}
        ] do
      Req.Test.expect(TaskBlockingCommands, fn conn -> Req.Test.json(conn, %{data: data}) end)
      result = run(argv)
      assert result.status == 5
      assert result.stdout == ""
      assert result.stderr =~ "invalid_response"
    end
  end

  test "missing edge and invalid graph link remain domain failures" do
    for {action, status, code} <- [
          {"remove", 404, "not_found"},
          {"add", 422, "validation_failed"}
        ] do
      Req.Test.expect(TaskBlockingCommands, fn conn ->
        conn
        |> Plug.Conn.put_status(status)
        |> Req.Test.json(%{
          error: %{
            code: code,
            message: "Link rejected",
            fields: %{target_task_id: ["Invalid link"]}
          }
        })
      end)

      result = run(["tasks", "blocks", action, "--project", "7", "12", "--target", "42"])
      assert result.status == 3
      assert result.stdout == ""
      assert result.stderr =~ code
    end
  end

  test "relationship commands reject invalid IDs locally" do
    for argv <- [
          ["tasks", "blocking", "show", "--project", "0", "42"],
          ["tasks", "blocking", "show", "--project", "9", "x"],
          ["tasks", "blocks", "add", "--project", "7", "12", "--target", "0"],
          ["tasks", "blocks", "remove", "--project", "7", "12", "--target", "abc"]
        ] do
      result = run(argv)
      assert result.status == 2
      assert result.stdout == ""
      assert result.stderr =~ "Invalid invocation"
    end
  end

  test "unconfirmed Done warning prints current unresolved blockers on stderr" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      assert conn.method == "PATCH"
      assert conn.request_path == "/api/v1/projects/9/tasks/42"
      assert Jason.decode!(Req.Test.raw_body(conn)) == %{"task" => %{"status" => "done"}}

      conn
      |> Plug.Conn.put_status(409)
      |> Req.Test.json(warning([@blocker]))
    end)

    result = run(["tasks", "update", "--project", "9", "42", "--status", "done"])
    assert result.status == 3
    assert result.stdout == ""

    for value <- [
          "unresolved_blockers",
          "12",
          "Approve copy",
          "in_review",
          "high",
          "7: Launch",
          "Review"
        ] do
      assert result.stderr =~ value
    end
  end

  test "known blocker IDs work on a first Done request and a retry" do
    for _attempt <- 1..2 do
      Req.Test.expect(TaskBlockingCommands, fn conn ->
        assert Jason.decode!(Req.Test.raw_body(conn)) == %{
                 "task" => %{"status" => "done"},
                 "confirmation" => %{"unresolved_blocker_ids" => [12, 15]}
               }

        Req.Test.json(conn, %{data: done_task()})
      end)

      result =
        run([
          "tasks",
          "update",
          "--project",
          "9",
          "42",
          "--status",
          "done",
          "--confirm-unresolved-blockers",
          "12,15"
        ])

      assert result.status == 0
      assert result.stderr == ""
      assert result.stdout =~ "STATUS: done"
    end
  end

  test "repeated blocker IDs are passed through to the API in a Done confirmation" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      assert Jason.decode!(Req.Test.raw_body(conn)) == %{
               "task" => %{"status" => "done"},
               "confirmation" => %{"unresolved_blocker_ids" => [15, 12, 15, 12]}
             }

      Req.Test.json(conn, %{data: done_task()})
    end)

    result =
      run(~w(tasks update --project 9 42 --status done --confirm-unresolved-blockers 15,12,15,12))

    assert result.status == 0
    assert result.stderr == ""
  end

  test "new blocker warning after confirmation is refreshed and preserves JSON error" do
    new_blocker = %{@blocker | id: 17, title: "Review legal"}

    Req.Test.expect(TaskBlockingCommands, fn conn ->
      assert Jason.decode!(Req.Test.raw_body(conn))["confirmation"] == %{
               "unresolved_blocker_ids" => [12]
             }

      conn |> Plug.Conn.put_status(409) |> Req.Test.json(warning([@blocker, new_blocker]))
    end)

    result =
      run([
        "tasks",
        "update",
        "--project",
        "9",
        "42",
        "--status",
        "done",
        "--confirm-unresolved-blockers",
        "12",
        "--json"
      ])

    assert result.status == 3
    assert result.stdout == ""

    assert %{"error" => %{"code" => "unresolved_blockers", "blockers" => blockers}} =
             Jason.decode!(result.stderr)

    assert Enum.map(blockers, & &1["id"]) == [12, 17]
  end

  test "force Done sends one case-specific override and uses ordinary success output" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      assert Jason.decode!(Req.Test.raw_body(conn)) == %{
               "task" => %{"status" => "done"},
               "confirmation" => %{"force_done_with_unresolved_blockers" => true}
             }

      Req.Test.json(conn, %{data: done_task()})
    end)

    result =
      run([
        "tasks",
        "update",
        "--project",
        "9",
        "42",
        "--status",
        "done",
        "--force-done-with-unresolved-blockers",
        "--json"
      ])

    assert result.status == 0
    assert result.stderr == ""
    assert %{"data" => %{"status" => "done"}} = Jason.decode!(result.stdout)
  end

  test "invalid Done confirmation options exit before HTTP" do
    base = ["tasks", "update", "--project", "9", "42"]

    for suffix <- [
          ["--status", "done", "--confirm-unresolved-blockers", ""],
          ["--status", "done", "--confirm-unresolved-blockers", "12,"],
          ["--status", "done", "--confirm-unresolved-blockers", "12,12,x"],
          ["--status", "done", "--confirm-unresolved-blockers", "12, 15"],
          ["--status", "done", "--confirm-unresolved-blockers", "12 ,15"],
          ["--status", "done", "--confirm-unresolved-blockers", "0"],
          ["--status", "done", "--confirm-unresolved-blockers", "12,x"],
          [
            "--status",
            "done",
            "--confirm-unresolved-blockers",
            "12",
            "--force-done-with-unresolved-blockers"
          ],
          ["--status", "pending", "--confirm-unresolved-blockers", "12"],
          ["--status", "pending", "--force-done-with-unresolved-blockers"],
          ["--force-done-with-unresolved-blockers"]
        ] do
      result = run(base ++ suffix)
      assert result.status == 2, inspect(suffix)
      assert result.stdout == ""
      assert result.stderr =~ "Invalid invocation"
    end
  end

  test "malformed blocker warning is an invalid response" do
    Req.Test.expect(TaskBlockingCommands, fn conn ->
      conn
      |> Plug.Conn.put_status(409)
      |> Req.Test.json(warning([Map.delete(@blocker, :priority)]))
    end)

    result = run(["tasks", "update", "--project", "9", "42", "--status", "done"])
    assert result.status == 5
    assert result.stderr =~ "invalid_response"
  end

  defp run(argv) do
    Taskman.CLI.run(argv,
      env: %{"TASKMAN_API_KEY" => "tm_blocking_test_credential"},
      config_root: Path.join(System.tmp_dir!(), "taskman-cli-blocking-tests"),
      req_options: [plug: {Req.Test, TaskBlockingCommands}]
    )
  end

  defp edge, do: %{blocking_task: @blocker, blocked_task: @blocked}

  defp warning(blockers),
    do: %{
      error: %{
        code: "unresolved_blockers",
        message: "Review unresolved direct blockers before marking Done",
        blockers: blockers
      }
    }

  defp done_task do
    %{
      id: 42,
      project_id: 9,
      list_id: nil,
      parent_task_id: nil,
      title: "Publish site",
      description: "",
      status: "done",
      priority: "urgent",
      due_at: nil,
      location: %{kind: "project", list_id: nil, path: []}
    }
  end
end
