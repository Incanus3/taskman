defmodule Taskman.CLI.ConfigTest do
  use ExUnit.Case, async: true

  import Bitwise

  alias Taskman.CLI.Config

  @api_key "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"

  @tag :tmp_dir
  test "resolves each setting independently with flag, environment, file, and default precedence",
       %{
         tmp_dir: tmp_dir
       } do
    root = Path.join(tmp_dir, "xdg")
    write_config(root, %{"api_url" => "https://file.example", "api_key" => @api_key})

    assert {:ok, %{api_url: "https://flag.example", api_key: @api_key}} =
             Config.resolve(
               %{api_url: "https://flag.example"},
               env: %{
                 "TASKMAN_API_URL" => "https://environment.example",
                 "TASKMAN_API_KEY" => @api_key
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

    assert Config.path(env: %{}) ==
             Path.join([System.user_home!(), ".config", "taskman", "config.json"])

    assert Config.path(env: %{"XDG_CONFIG_HOME" => "", "HOME" => ""}) ==
             Path.join([System.user_home!(), ".config", "taskman", "config.json"])
  end

  @tag :tmp_dir
  test "rejects malformed, unknown, unreadable, and permissive configuration files", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    path = config_path(root)
    File.mkdir_p!(Path.dirname(path))
    File.chmod!(Path.dirname(path), 0o700)

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
               end,
               sync_directory: fn directory ->
                 assert directory == Path.dirname(target)
                 :ok
               end
             )

    assert {:ok, %{api_url: "https://replacement.taskman.example", api_key: @api_key}} =
             Config.resolve(%{}, config_root: root)

    assert File.ls!(Path.dirname(target)) == ["config.json"]
  end

  @tag :tmp_dir
  test "never steals an existing lock, even when it appears aged", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    write_config(root, %{"api_key" => @api_key})
    lock_path = target <> ".lock"
    parent = self()

    url_task =
      Task.async(fn ->
        Config.set_url("https://held-lock.example",
          config_root: root,
          before_write: fn _path ->
            send(parent, :writer_holds_lock)
            assert_receive :finish_held_writer
            :ok
          end
        )
      end)

    assert_receive :writer_holds_lock

    # A writer can remain live while its lock looks arbitrarily old (for example
    # after a clock adjustment).  Age is never evidence that it is safe to take.
    assert :ok = File.touch(lock_path, {{2000, 1, 1}, {0, 0, 0}})

    assert {:error, :invalid_configuration, message} =
             Config.set_key(@api_key,
               config_root: root,
               lock_retry_limit: 0
             )

    assert message =~ "busy"
    assert File.lstat!(lock_path).type == :directory

    send(url_task.pid, :finish_held_writer)
    assert :ok = Task.await(url_task)

    assert {:ok, %{api_url: "https://held-lock.example", api_key: @api_key}} =
             Config.resolve(%{}, config_root: root)

    refute File.exists?(lock_path)
  end

  @tag :tmp_dir
  test "leaves aged, foreign, malformed, and symlink locks untouched", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    write_config(root, %{"api_url" => "https://before-lock.example", "api_key" => @api_key})
    lock_path = target <> ".lock"
    linked_directory = Path.join(tmp_dir, "linked-lock-directory")
    File.mkdir_p!(linked_directory)

    locks = [
      {:aged_owned,
       fn ->
         File.mkdir!(lock_path)
         File.chmod!(lock_path, 0o700)
         File.write!(Path.join(lock_path, "owner"), "taskman_config_lock_v1:abandoned")
         :ok = File.touch(lock_path, {{2000, 1, 1}, {0, 0, 0}})
       end},
      {:foreign,
       fn ->
         File.mkdir!(lock_path)
         File.chmod!(lock_path, 0o700)
         File.write!(Path.join(lock_path, "owner"), "foreign-lock")
       end},
      {:malformed, fn -> File.write!(lock_path, "not a lock directory") end},
      {:symlink, fn -> File.ln_s!(linked_directory, lock_path) end}
    ]

    for {_kind, create_lock} <- locks do
      create_lock.()

      assert {:error, :invalid_configuration, message} =
               Config.set_url("https://after-lock.example",
                 config_root: root,
                 lock_retry_limit: 0
               )

      assert message =~ "busy"
      assert Jason.decode!(File.read!(target))["api_url"] == "https://before-lock.example"
      assert {:ok, _stat} = File.lstat(lock_path)
      File.rm_rf!(lock_path)
    end
  end

  @tag :tmp_dir
  test "releases only the lock token acquired by its own writer", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    write_config(root, %{"api_key" => @api_key})
    lock_path = target <> ".lock"

    assert :ok =
             Config.set_url("https://token-owner.example",
               config_root: root,
               before_release: fn ^lock_path ->
                 File.rm_rf!(lock_path)
                 File.mkdir!(lock_path)
                 File.chmod!(lock_path, 0o700)
                 File.write!(Path.join(lock_path, "owner"), "taskman_config_lock_v1:foreign")
                 :ok
               end
             )

    assert File.read!(Path.join(lock_path, "owner")) == "taskman_config_lock_v1:foreign"
    assert Jason.decode!(File.read!(target))["api_url"] == "https://token-owner.example"
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
  test "serializes concurrent field updates so neither setting is lost", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    parent = self()

    url_task =
      Task.async(fn ->
        Config.set_url("https://concurrent.example",
          config_root: root,
          before_write: fn _path ->
            send(parent, :url_write_has_read_config)
            assert_receive :finish_url_write
            :ok
          end
        )
      end)

    assert_receive :url_write_has_read_config

    key_task = Task.async(fn -> Config.set_key(@api_key, config_root: root) end)
    send(url_task.pid, :finish_url_write)

    assert :ok = Task.await(url_task)
    assert :ok = Task.await(key_task)

    assert {:ok, %{api_url: "https://concurrent.example", api_key: @api_key}} =
             Config.resolve(%{}, config_root: root)
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

  @tag :tmp_dir
  test "requires exact private modes for existing configuration directories and files", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    path = config_path(root)
    File.mkdir_p!(Path.dirname(path))
    File.chmod!(Path.dirname(path), 0o700)
    File.write!(path, Jason.encode!(%{"api_key" => @api_key}))
    File.chmod!(path, 0o600)
    File.chmod!(Path.dirname(path), 0o755)

    assert {:error, :invalid_configuration, directory_message} =
             Config.resolve(%{}, config_root: root)

    assert directory_message =~ "directory permissions"

    File.chmod!(Path.dirname(path), 0o700)
    File.chmod!(path, 0o400)

    assert {:error, :invalid_configuration, file_message} = Config.resolve(%{}, config_root: root)
    assert file_message =~ "chmod 600"
  end

  @tag :tmp_dir
  test "rejects reported special permission bits on configuration paths", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    path = config_path(root)
    write_config(root, %{"api_key" => @api_key})
    directory = Path.dirname(path)

    File.chmod!(path, 0o4600)
    assert {:ok, %File.Stat{mode: file_mode}} = File.stat(path)

    if (file_mode &&& 0o7000) != 0 do
      assert {:error, :invalid_configuration, file_message} =
               Config.resolve(%{}, config_root: root)

      assert file_message =~ "chmod 600"
    end

    File.chmod!(path, 0o600)
    File.chmod!(directory, 0o1700)
    assert {:ok, %File.Stat{mode: directory_mode}} = File.stat(directory)

    if (directory_mode &&& 0o7000) != 0 do
      assert {:error, :invalid_configuration, directory_message} =
               Config.resolve(%{}, config_root: root)

      assert directory_message =~ "directory permissions"
    end
  end

  @tag :tmp_dir
  test "rejects linked config files and descriptor identity replacements", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    path = config_path(root)
    linked_contents = Jason.encode!(%{"api_key" => @api_key})
    File.mkdir_p!(Path.dirname(path))
    File.chmod!(Path.dirname(path), 0o700)

    linked_target = Path.join(tmp_dir, "linked-config.json")
    File.write!(linked_target, linked_contents)
    File.chmod!(linked_target, 0o600)
    File.ln_s!(linked_target, path)

    assert {:error, :invalid_configuration, symlink_message} =
             Config.resolve(%{}, config_root: root)

    assert symlink_message =~ "regular file"

    File.rm!(path)
    File.ln!(linked_target, path)

    assert {:error, :invalid_configuration, hardlink_message} =
             Config.resolve(%{}, config_root: root)

    assert hardlink_message =~ "hard links"

    File.rm!(path)
    File.write!(path, linked_contents)
    File.chmod!(path, 0o600)

    assert {:error, :invalid_configuration, replacement_message} =
             Config.resolve(%{},
               config_root: root,
               before_open: fn config_path ->
                 File.rm!(config_path)

                 File.write!(
                   config_path,
                   Jason.encode!(%{
                     "api_key" => @api_key,
                     "api_url" => "https://replacement.example"
                   })
                 )

                 File.chmod!(config_path, 0o600)
               end
             )

    assert replacement_message =~ "changed while it was being read"
  end

  @tag :tmp_dir
  test "uses higher-precedence values without validating an ignored lower-precedence URL", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    write_config(root, %{"api_url" => "ftp://ignored.example", "api_key" => @api_key})

    assert {:ok, %{api_url: "https://override.example", api_key: @api_key}} =
             Config.resolve(%{api_url: "https://override.example"},
               config_root: root,
               env: %{"TASKMAN_API_KEY" => @api_key}
             )
  end

  @tag :tmp_dir
  test "accepts only structurally valid Taskman keys and constrained base URLs", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")

    assert :ok = Config.set_url("https://[::1]:4001/", config_root: root)
    assert :ok = Config.set_url("http://localhost:4000", config_root: root)

    for url <- [
          "https://taskman.example/path",
          "https://taskman.example:0",
          "https://taskman.example:65536",
          "https://user:password@taskman.example",
          "ssh://taskman.example"
        ] do
      assert {:error, :invalid_configuration, _message} = Config.set_url(url, config_root: root)
    end

    for key <- [
          "tm_missing",
          "tm_payload_",
          "tm_payload_checksum\n",
          "tm_payload checksum"
        ] do
      assert {:error, :invalid_configuration, _message} = Config.set_key(key, config_root: root)
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
    File.chmod!(Path.dirname(path), 0o700)
    File.write!(path, Jason.encode!(contents))
    File.chmod!(path, 0o600)
  end

  defp config_path(root), do: Path.join([root, "taskman", "config.json"])
end
