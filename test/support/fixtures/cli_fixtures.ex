defmodule Taskman.CLI.Fixtures do
  @moduledoc "Disposable CLI configuration and Git fixtures."

  alias Taskman.CLI.Config.Document
  alias Taskman.CLI.Context

  def activity_entry(overrides \\ %{}) do
    reference = %{"id" => 42, "project_id" => 7, "project_name" => "Demo", "title" => "Review"}

    change = %{
      "type" => "field_changed",
      "task_id" => 42,
      "field" => "priority",
      "actor" => %{
        "user_id" => "00000000-0000-4000-8000-000000000001",
        "login" => "person@example.invalid"
      },
      "before" => "low",
      "after" => "high",
      "context" => %{"task" => reference},
      "first_changed_at" => "2026-11-07T12:00:00Z",
      "last_changed_at" => "2026-11-07T12:00:30Z"
    }

    %{
      "id" => "event-1",
      "kind" => "change",
      "occurred_at" => "2026-11-07T12:00:30Z",
      "change" => Map.merge(change, overrides)
    }
  end

  def activity_comment do
    %{
      "id" => "comment-2",
      "kind" => "comment",
      "occurred_at" => "2026-11-07T12:00:40Z",
      "comment" => %{
        "id" => 2,
        "task_id" => 42,
        "text" => "Review notes",
        "author" => %{"display_name" => "Research agent", "login" => "person@example.invalid"},
        "created_at" => "2026-11-07T12:00:40Z"
      }
    }
  end

  def resolved_context(overrides \\ %{}) do
    struct(
      Context,
      Map.merge(
        %{
          host: "personal",
          api_url: "https://personal.example.invalid",
          api_key: "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"
        },
        overrides
      )
    )
  end

  def run_with_host(args, options) do
    options = Map.new(options)

    root =
      Path.join(System.tmp_dir!(), "taskman-host-fixture-#{System.unique_integer([:positive])}")

    key = if Map.get(options, :authenticated?, true), do: resolved_context().api_key, else: nil

    write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example.invalid", api_key: key}}
    })

    ExUnit.Callbacks.on_exit(fn -> File.rm_rf!(root) end)

    Taskman.CLI.run(
      args,
      options
      |> Map.put(:config_root, root)
      |> Map.put(:env, %{})
      |> Map.put(:git_runner, &outside_git/3)
    )
  end

  def outside_git(_argv, _cwd, _env),
    do: {"fatal: not a git repository (or any of the parent directories): .git\n", 128}

  def write_hosts!(root, document), do: write_config!(root, Document.encode(document))

  def legacy_config!(root, settings), do: write_config!(root, settings)

  def git_repo!(tmp_dir, name) do
    path = Path.join(tmp_dir, name)
    File.mkdir_p!(path)
    git!(path, ["init", "--quiet"])

    git!(path, [
      "-c",
      "user.name=CLI Fixture",
      "-c",
      "user.email=fixture@example.invalid",
      "commit",
      "--quiet",
      "--allow-empty",
      "-m",
      "Disposable fixture"
    ])

    %{cwd: path, common_dir: Path.join(path, ".git")}
  end

  def linked_worktree!(repo, path) do
    git!(repo.cwd, ["worktree", "add", "--quiet", "--detach", path])
    %{cwd: path, common_dir: repo.common_dir}
  end

  def git!(cwd, argv) do
    unset = for {key, _} <- System.get_env(), String.starts_with?(key, "GIT_"), do: {key, nil}

    env =
      unset ++ [{"GIT_CONFIG_GLOBAL", "/dev/null"}, {"GIT_CONFIG_NOSYSTEM", "1"}, {"LC_ALL", "C"}]

    case System.cmd("git", argv, cd: cwd, env: env, stderr_to_stdout: true) do
      {output, 0} -> output
      {output, status} -> raise "Disposable Git fixture failed (#{status}): #{output}"
    end
  end

  defp write_config!(root, settings) do
    path = Path.join([root, "taskman", "config.json"])
    File.mkdir_p!(Path.dirname(path))
    File.chmod!(Path.dirname(path), 0o700)
    File.write!(path, Jason.encode!(settings) <> "\n")
    File.chmod!(path, 0o600)
    path
  end
end
