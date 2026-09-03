defmodule Taskman.CLI.ConfigTest do
  use ExUnit.Case, async: true

  import Bitwise

  alias Taskman.CLI.Config

  @api_key "tm_config_test_credential"

  @tag :tmp_dir
  test "resolves each setting independently with flag, environment, file, and default precedence",
       %{
         tmp_dir: tmp_dir
       } do
    root = Path.join(tmp_dir, "xdg")
    write_config(root, %{"api_url" => "https://file.example", "api_key" => @api_key})

    assert {:ok, %{api_url: "https://flag.example", api_key: "tm_environment_credential"}} =
             Config.resolve(
               %{api_url: "https://flag.example"},
               env: %{
                 "TASKMAN_API_URL" => "https://environment.example",
                 "TASKMAN_API_KEY" => "tm_environment_credential"
               },
               config_root: root
             )

    assert {:ok, %{api_url: "https://environment.example", api_key: @api_key}} =
             Config.resolve(%{},
               env: %{"TASKMAN_API_URL" => "https://environment.example"},
               config_root: root
             )

    assert {:ok, %{api_url: "http://localhost:4000", api_key: nil}} =
             Config.resolve(%{}, env: %{}, config_root: Path.join(tmp_dir, "empty"))
  end

  @tag :tmp_dir
  test "derives the standard XDG path from injected environment values", %{tmp_dir: tmp_dir} do
    assert Config.path(env: %{"XDG_CONFIG_HOME" => tmp_dir}) ==
             Path.join([tmp_dir, "taskman", "config.json"])

    assert Config.path(env: %{"HOME" => tmp_dir}) ==
             Path.join([tmp_dir, ".config", "taskman", "config.json"])
  end

  @tag :tmp_dir
  test "rejects malformed, unknown, unreadable, and permissive configuration files", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    path = config_path(root)
    File.mkdir_p!(Path.dirname(path))

    File.write!(path, "not-json")
    :ok = File.chmod(path, 0o600)
    assert {:error, :invalid_configuration, message} = Config.resolve(%{}, config_root: root)
    assert message =~ "valid JSON"
    refute message =~ "not-json"

    File.write!(path, Jason.encode!(["not", "an", "object"]))
    assert {:error, :invalid_configuration, message} = Config.resolve(%{}, config_root: root)
    assert message =~ "contain an object"

    File.write!(path, Jason.encode!(%{"api_url" => "https://file.example", "extra" => true}))
    assert {:error, :invalid_configuration, message} = Config.resolve(%{}, config_root: root)
    assert message =~ "only api_url and api_key"

    File.write!(path, Jason.encode!(%{"api_key" => 7}))
    assert {:error, :invalid_configuration, message} = Config.resolve(%{}, config_root: root)
    assert message =~ "strings"

    File.write!(path, Jason.encode!(%{"api_key" => @api_key}))
    File.chmod!(path, 0o644)
    assert {:error, :invalid_configuration, message} = Config.resolve(%{}, config_root: root)
    assert message =~ "chmod 600"
    refute message =~ @api_key

    unreadable_root = Path.join(tmp_dir, "unreadable")
    write_config(unreadable_root, %{"api_key" => @api_key})

    assert {:error, :invalid_configuration, message} =
             Config.resolve(%{},
               config_root: unreadable_root,
               read_file: fn _path -> {:error, :eacces} end
             )

    assert message =~ "could not be read"
  end

  @tag :tmp_dir
  test "creates a private directory and file with sibling staging and atomic replacement", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)

    assert :ok = Config.set_url("https://hosted.taskman.example", config_root: root)
    assert :ok = Config.set_key(@api_key, config_root: root)

    assert {:ok, %File.Stat{mode: directory_mode}} = File.stat(Path.dirname(target))
    assert (directory_mode &&& 0o077) == 0

    assert {:ok, %File.Stat{mode: file_mode}} = File.stat(target)
    assert (file_mode &&& 0o777) == 0o600

    assert Jason.decode!(File.read!(target)) == %{
             "api_key" => @api_key,
             "api_url" => "https://hosted.taskman.example"
           }

    assert :ok =
             Config.set_url("https://replacement.taskman.example",
               config_root: root,
               rename: fn from, to ->
                 assert Path.dirname(from) == Path.dirname(to)
                 assert Path.basename(from) =~ ~r/\A\.config\.json\.stage-/
                 File.rename(from, to)
               end
             )

    assert {:ok, %{api_url: "https://replacement.taskman.example", api_key: @api_key}} =
             Config.resolve(%{}, config_root: root)

    assert File.ls!(Path.dirname(target)) == ["config.json"]
  end

  @tag :tmp_dir
  test "fails safely when atomic replacement cannot rename the sibling stage", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    write_config(root, %{"api_url" => "https://before.example", "api_key" => @api_key})

    assert {:error, :invalid_configuration, message} =
             Config.set_url("https://after.example",
               config_root: root,
               rename: fn _from, _to -> {:error, :eacces} end
             )

    assert message =~ "atomically replace"
    refute message =~ @api_key
    assert Jason.decode!(File.read!(target))["api_url"] == "https://before.example"
    assert File.ls!(Path.dirname(target)) == ["config.json"]
  end

  @tag :tmp_dir
  test "rejects unsafe configuration directories and invalid API URLs", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    directory = Path.join(root, "taskman")
    File.mkdir_p!(directory)
    File.chmod!(directory, 0o770)

    assert {:error, :invalid_configuration, message} =
             Config.set_key(@api_key, config_root: root)

    assert message =~ "directory permissions"

    for url <- [
          "ftp://taskman.example",
          "https://user:pass@taskman.example",
          "https://taskman.example?credential=not-allowed",
          "https://taskman.example#fragment",
          "not a URL"
        ] do
      assert {:error, :invalid_configuration, message} = Config.set_url(url, config_root: root)
      assert message =~ "valid HTTP or HTTPS"
    end
  end

  test "display confirms a configured key without revealing it" do
    assert Config.display(%{api_url: "https://taskman.example", api_key: @api_key}) == %{
             api_url: "https://taskman.example",
             api_key_configured: true
           }
  end

  defp write_config(root, contents) do
    path = config_path(root)
    File.mkdir_p!(Path.dirname(path))
    File.write!(path, Jason.encode!(contents))
    File.chmod!(path, 0o600)
  end

  defp config_path(root), do: Path.join([root, "taskman", "config.json"])
end
