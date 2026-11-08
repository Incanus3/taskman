defmodule Taskman.CLI.Commands.ActivityTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Fixtures
  setup {Req.Test, :verify_on_exit!}

  test "events-only and mixed queries preserve timeline order in readable and JSON modes" do
    for comments? <- [false, true], json? <- [false, true] do
      entries =
        [Fixtures.activity_entry()] ++ if(comments?, do: [Fixtures.activity_comment()], else: [])

      Req.Test.expect(ActivityCommands, fn conn ->
        assert conn.method == "GET"
        assert conn.request_path == "/api/v1/projects/7/tasks/42/activity"
        assert conn.query_string == if(comments?, do: "include_comments=true", else: "")
        Req.Test.json(conn, %{data: entries})
      end)

      args =
        ~w(tasks activity list --project 7 42) ++
          if(comments?, do: ["--include-comments"], else: []) ++
          if(json?, do: ["--json"], else: [])

      result = run(args)
      assert result.status == 0, result.stderr
      assert result.stderr == ""

      if json? do
        assert Jason.decode!(result.stdout) == %{"data" => entries}
      else
        assert result.stdout =~ "person@example.invalid"
        assert result.stdout =~ "Priority changed from low to high"

        if comments? do
          assert result.stdout =~ ~r/Priority changed.*Research agent.*Review notes/s
        end
      end
    end
  end

  test "activity inherits the repository Project" do
    root = Path.join(System.tmp_dir!(), "activity-cli-#{System.unique_integer([:positive])}")
    on_exit(fn -> File.rm_rf!(root) end)
    repo = Fixtures.git_repo!(root, "clone")
    File.mkdir_p!(Path.join(repo.common_dir, "taskman"))

    File.write!(
      Path.join([repo.common_dir, "taskman", "binding.json"]),
      ~s({"version":1,"host":"personal","project_id":7})
    )

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: nil,
      hosts: %{
        "personal" => %{
          api_url: "https://personal.example.invalid",
          api_key: Fixtures.resolved_context().api_key
        }
      }
    })

    Req.Test.expect(ActivityCommands, fn conn ->
      assert conn.request_path == "/api/v1/projects/7/tasks/42/activity"
      Req.Test.json(conn, %{data: []})
    end)

    result =
      Taskman.CLI.run(~w(tasks activity list 42),
        config_root: root,
        cwd: repo.cwd,
        env: %{},
        req_options: [plug: {Req.Test, ActivityCommands}]
      )

    assert result.status == 0, result.stderr
    assert result.stdout == "No activity yet.\n"
  end

  test "malformed successful timelines are contract errors in both output modes" do
    base = Fixtures.activity_entry()

    bad = [
      %{base | "kind" => "event"},
      %{base | "occurred_at" => "invalid"},
      Fixtures.activity_entry(%{
        "type" => "block_changed",
        "field" => nil,
        "before" => false,
        "after" => true
      }),
      Fixtures.activity_entry(%{
        "type" => "task_created",
        "field" => nil,
        "before" => nil,
        "after" => nil
      }),
      Fixtures.activity_entry(%{"field" => "description"})
    ]

    for entry <- bad, json? <- [false, true] do
      Req.Test.expect(ActivityCommands, fn conn -> Req.Test.json(conn, %{data: [entry]}) end)
      args = ~w(tasks activity list --project 7 42) ++ if(json?, do: ["--json"], else: [])
      result = run(args)
      assert result.status == 5
      assert result.stdout == ""
      assert result.stderr =~ "invalid_response"
    end
  end

  defp run(args),
    do: Fixtures.run_with_host(args, req_options: [plug: {Req.Test, ActivityCommands}])
end
