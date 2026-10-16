defmodule Taskman.CLI.Commands.ConfigTest do
  use ExUnit.Case, async: true
  alias Taskman.CLI.{Config, Fixtures}
  alias Taskman.FakeTerminal
  @key "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"
  @moduletag :tmp_dir
  setup %{tmp_dir: tmp_dir} do
    %{
      opts: [
        config_root: Path.join(tmp_dir, "config"),
        cwd: tmp_dir,
        env: %{},
        git_runner: &Fixtures.outside_git/3
      ]
    }
  end

  test "host registration is keyless and default selection is explicit", %{opts: opts} do
    result =
      Taskman.CLI.run(
        ~w(config hosts add personal --url https://taskman.example.com --json),
        opts
      )

    assert result.status == 0
    assert result.stderr == ""
    assert Jason.decode!(result.stdout) == %{"data" => %{"saved" => true}}
    show = Taskman.CLI.run(~w(config hosts show personal --json), opts)

    assert Jason.decode!(show.stdout) == %{
             "data" => %{
               "name" => "personal",
               "api_url" => "https://taskman.example.com",
               "api_key_configured" => false,
               "default" => false
             }
           }

    assert Jason.decode!(Taskman.CLI.run(~w(config default-host personal --json), opts).stdout) ==
             %{"data" => %{"saved" => true}}

    assert {:ok, %{default_host: "personal"}} = Config.load(opts)

    assert Taskman.CLI.run(~w(config hosts add personal --url https://other.example --json), opts).status ==
             2
  end

  test "readable host receipts and inspection remain newline terminated", %{opts: opts} do
    for {argv, stdout} <- [
          {~w(config hosts list), "\n"},
          {~w(config hosts add personal --url https://personal.example), "Saved Taskman host.\n"},
          {~w(config hosts show personal),
           "personal\thttps://personal.example\tnot configured\n"},
          {~w(config default-host personal), "Saved default Taskman host.\n"},
          {~w(config hosts list),
           "personal\thttps://personal.example\tnot configured\tdefault\n"},
          {~w(config hosts set-url personal --url https://moved.example),
           "All repositories bound to this host use the updated URL. API key retained.\n"},
          {~w(config show),
           "Host: personal\nAPI URL: https://moved.example\nAPI key: not configured\nProject: none\n"},
          {~w(config hosts remove personal), "Removed Taskman host.\n"}
        ] do
      result = Taskman.CLI.run(argv, opts)
      assert result.status == 0
      assert result.stdout == stdout
      assert result.stderr == ""
    end
  end

  test "secret prompting checks host existence before input and again under lock", %{opts: opts} do
    FakeTerminal.set_responses([], [@key])

    assert Taskman.CLI.run(
             ~w(config hosts set-key personal --json),
             Keyword.put(opts, :terminal, FakeTerminal)
           ).status == 2

    assert FakeTerminal.prompts() == []
    :ok = Config.add_host("personal", "https://personal.example", opts)

    FakeTerminal.set_responses([], [
      fn ->
        refute File.exists?(Config.path(opts) <> ".lock")
        @key
      end
    ])

    result =
      Taskman.CLI.run(
        ~w(config hosts set-key personal --json),
        Keyword.put(opts, :terminal, FakeTerminal)
      )

    assert result.status == 0
    assert Jason.decode!(result.stdout) == %{"data" => %{"saved" => true}}
    assert FakeTerminal.prompts() == [secret: "Taskman API key: "]
    refute result.stdout =~ @key
    refute result.stderr =~ @key
    assert {:ok, %{hosts: %{"personal" => %{api_key: @key}}}} = Config.load(opts)
  end

  test "host removal during secret prompting prevents recreation", %{opts: opts} do
    :ok = Config.add_host("personal", "https://personal.example", opts)

    FakeTerminal.set_responses([], [
      fn ->
        refute File.exists?(Config.path(opts) <> ".lock")
        :ok = Config.remove_host("personal", opts)
        @key
      end
    ])

    result =
      Taskman.CLI.run(
        ~w(config hosts set-key personal --json),
        Keyword.put(opts, :terminal, FakeTerminal)
      )

    assert result.status == 2
    assert Jason.decode!(result.stderr)["error"]["code"] == "invalid_invocation"
    assert {:ok, %{hosts: %{}}} = Config.load(opts)
  end

  test "secret saving rechecks host existence while holding the config lock", %{opts: opts} do
    :ok = Config.add_host("personal", "https://personal.example", opts)
    FakeTerminal.set_responses([], [@key])

    options =
      opts
      |> Keyword.put(:terminal, FakeTerminal)
      |> Keyword.put(:before_open, fn path ->
        if File.dir?(path <> ".lock") do
          empty = Jason.encode!(%{version: 2, hosts: %{}})
          File.write!(path, String.pad_trailing(empty, File.stat!(path).size))
          send(self(), :checked_under_lock)
        end
      end)

    result = Taskman.CLI.run(~w(config hosts set-key personal --json), options)
    assert_received :checked_under_lock
    assert result.status == 2
    assert Jason.decode!(result.stderr)["error"]["code"] == "invalid_invocation"
    assert {:ok, %{hosts: %{}}} = Config.load(opts)
  end

  test "local recovery ignores transport variables", %{opts: opts} do
    opts =
      Keyword.put(opts, :env, %{
        "TASKMAN_API_URL" => "private invalid",
        "TASKMAN_HOST" => "INVALID",
        "TASKMAN_API_KEY" => "private invalid"
      })

    assert Taskman.CLI.run(
             ~w(config hosts add personal --url https://personal.example --json),
             opts
           ).status == 0

    :ok = Config.set_host_key("personal", @key, opts)
    :ok = Config.set_default_host("personal", opts)

    result =
      Taskman.CLI.run(~w(config hosts set-url personal --url https://moved.example --json), opts)

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "saved" => true,
               "message" =>
                 "All repositories bound to this host use the updated URL. API key retained."
             }
           }

    assert {:ok, %{hosts: %{"personal" => %{api_key: @key}}}} = Config.load(opts)

    assert Jason.decode!(Taskman.CLI.run(~w(config hosts remove personal --json), opts).stdout) ==
             %{"data" => %{"removed" => true}}

    assert {:ok, %{default_host: nil, hosts: %{}}} = Config.load(opts)
  end

  test "effective inspection reports exact safe provenance", %{opts: opts} do
    Fixtures.write_hosts!(opts[:config_root], %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: "https://personal.example", api_key: @key}}
    })

    result = Taskman.CLI.run(~w(config show --json), opts)

    assert Jason.decode!(result.stdout) == %{
             "data" => %{
               "config_format" => "v2",
               "default_host" => "personal",
               "host" => "personal",
               "api_url" => "https://personal.example",
               "api_key_configured" => true,
               "project_id" => nil,
               "sources" => %{"host" => "default", "project" => "none", "key" => "stored"}
             }
           }

    refute result.stdout =~ @key
    assert Taskman.CLI.run(~w(config show --project 7 --json), opts).status == 2
    assert Taskman.CLI.run(~w(config hosts list --host personal --json), opts).status == 2
  end

  test "stored host list is sorted and excludes injected credentials", %{opts: opts} do
    :ok = Config.add_host("work", "https://work.example", opts)
    :ok = Config.add_host("personal", "https://personal.example", opts)
    :ok = Config.set_host_key("personal", @key, opts)
    :ok = Config.set_default_host("work", opts)

    result =
      Taskman.CLI.run(
        ~w(config hosts list --json),
        Keyword.put(opts, :env, %{"TASKMAN_API_KEY" => "private invalid"})
      )

    assert Jason.decode!(result.stdout) == %{
             "data" => [
               %{
                 "name" => "personal",
                 "api_url" => "https://personal.example",
                 "api_key_configured" => true,
                 "default" => false
               },
               %{
                 "name" => "work",
                 "api_url" => "https://work.example",
                 "api_key_configured" => false,
                 "default" => true
               }
             ]
           }

    refute result.stdout =~ @key
  end

  test "missing or invalid secret input is a local error", %{opts: opts} do
    :ok = Config.add_host("personal", "https://personal.example", opts)

    for {input, status} <- [{:unavailable, 5}, {:invalid, 2}] do
      FakeTerminal.set_responses([], [
        if(input == :unavailable, do: {:error, :input_unavailable}, else: "private invalid")
      ])

      result =
        Taskman.CLI.run(
          ~w(config hosts set-key personal --json),
          Keyword.put(opts, :terminal, FakeTerminal)
        )

      assert result.status == status
      assert result.stdout == ""
      refute result.stderr =~ "private invalid"
    end
  end
end
