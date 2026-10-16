defmodule Taskman.CLI.ConfigTest do
  use ExUnit.Case, async: true

  import Bitwise

  alias Taskman.CLI.Config
  alias Taskman.CLI.Fixtures

  @api_key "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"
  @coordination_timeout 5_000

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
    assert {:error, :invalid_configuration, message} = load_default(config_root: root)
    assert message =~ "valid JSON"
    refute message =~ "not-json"

    File.write!(path, Jason.encode!(["not", "an", "object"]))
    assert {:error, :invalid_configuration, message} = load_default(config_root: root)
    assert message =~ "contain an object"

    File.write!(path, Jason.encode!(%{"api_url" => "https://file.example", "extra" => true}))
    assert {:error, :invalid_configuration, message} = load_default(config_root: root)
    assert message =~ "unsupported fields"

    File.write!(path, Jason.encode!(%{"api_key" => 7}))
    assert {:error, :invalid_configuration, message} = load_default(config_root: root)
    assert message =~ "valid server-issued credential"

    File.write!(path, Jason.encode!(%{"api_key" => @api_key}))
    File.chmod!(path, 0o644)
    assert {:error, :invalid_configuration, message} = load_default(config_root: root)
    assert message =~ "chmod 600"
    refute message =~ @api_key

    unreadable_root = Path.join(tmp_dir, "unreadable")
    write_config(unreadable_root, %{"api_key" => @api_key})

    assert {:error, :invalid_configuration, message} =
             load_default(
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

    assert :ok = set_default_url("https://hosted.taskman.example", config_root: root)
    assert :ok = Config.set_host_key("default", @api_key, config_root: root)

    assert {:ok, %File.Stat{mode: directory_mode}} = File.stat(Path.dirname(target))
    assert (directory_mode &&& 0o077) == 0

    assert {:ok, %File.Stat{mode: file_mode}} = File.stat(target)
    assert (file_mode &&& 0o777) == 0o600

    assert Jason.decode!(File.read!(target))["hosts"]["default"] == %{
             "api_key" => @api_key,
             "api_url" => "https://hosted.taskman.example"
           }

    assert :ok =
             set_default_url("https://replacement.taskman.example",
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
             load_default(config_root: root)

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
        set_default_url("https://held-lock.example",
          config_root: root,
          before_write: fn _path ->
            send(parent, :writer_holds_lock)
            assert_receive :finish_held_writer, @coordination_timeout
            :ok
          end
        )
      end)

    assert_receive :writer_holds_lock, @coordination_timeout

    # A writer can remain live while its lock looks arbitrarily old (for example
    # after a clock adjustment).  Age is never evidence that it is safe to take.
    assert :ok = File.touch(lock_path, {{2000, 1, 1}, {0, 0, 0}})

    assert {:error, :invalid_configuration, message} =
             Config.set_host_key("default", @api_key,
               config_root: root,
               lock_retry_limit: 0
             )

    assert message =~ "busy"
    assert File.lstat!(lock_path).type == :directory

    send(url_task.pid, :finish_held_writer)
    assert :ok = Task.await(url_task)

    assert {:ok, %{api_url: "https://held-lock.example", api_key: @api_key}} =
             load_default(config_root: root)

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
               set_default_url("https://after-lock.example",
                 config_root: root,
                 lock_retry_limit: 0
               )

      assert message =~ "busy"
      assert stored_default_url(target) == "https://before-lock.example"
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
             set_default_url("https://token-owner.example",
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
    assert stored_default_url(target) == "https://token-owner.example"
  end

  @tag :tmp_dir
  test "release leaves a replacement writer's lock intact after ownership validation", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    lock_path = target <> ".lock"
    parent = self()
    write_config(root, %{"api_key" => @api_key})

    assert :ok =
             set_default_url("https://first-writer.example",
               config_root: root,
               after_lock_validation: fn ^lock_path ->
                 File.rm_rf!(lock_path)

                 replacement_task =
                   Task.async(fn ->
                     Config.set_host_key("default", @api_key,
                       config_root: root,
                       before_write: fn _path ->
                         send(parent, :replacement_writer_holds_lock)
                         assert_receive :finish_replacement_writer, @coordination_timeout
                         :ok
                       end
                     )
                   end)

                 send(parent, {:replacement_writer, replacement_task})
                 assert_receive :replacement_writer_holds_lock, @coordination_timeout
                 :ok
               end
             )

    assert_receive {:replacement_writer, replacement_task}, @coordination_timeout

    assert {:error, :invalid_configuration, message} =
             set_default_url("https://third-writer.example",
               config_root: root,
               lock_retry_limit: 0
             )

    assert message =~ "busy"
    assert {:ok, %File.Stat{type: :directory}} = File.lstat(lock_path)

    send(replacement_task.pid, :finish_replacement_writer)
    assert :ok = Task.await(replacement_task)

    assert {:ok, %{api_url: "https://first-writer.example", api_key: @api_key}} =
             load_default(config_root: root)

    refute File.exists?(lock_path)
  end

  @tag :tmp_dir
  test "fails safely when atomic replacement cannot rename the sibling stage", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    target = config_path(root)
    write_config(root, %{"api_url" => "https://before.example", "api_key" => @api_key})

    assert {:error, :invalid_configuration, message} =
             set_default_url("https://after.example",
               config_root: root,
               rename: fn _from, _to -> {:error, :eacces} end
             )

    assert message =~ "atomically replace"
    refute message =~ @api_key
    assert stored_default_url(target) == "https://before.example"
    assert File.ls!(Path.dirname(target)) == ["config.json"]
  end

  @tag :tmp_dir
  test "serializes concurrent field updates so neither setting is lost", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    parent = self()
    write_config(root, %{"api_url" => "https://before.example"})

    url_task =
      Task.async(fn ->
        set_default_url("https://concurrent.example",
          config_root: root,
          before_write: fn _path ->
            send(parent, :url_write_has_read_config)
            assert_receive :finish_url_write, @coordination_timeout
            :ok
          end
        )
      end)

    assert_receive :url_write_has_read_config, @coordination_timeout

    key_task = Task.async(fn -> Config.set_host_key("default", @api_key, config_root: root) end)
    send(url_task.pid, :finish_url_write)

    assert :ok = Task.await(url_task)
    assert :ok = Task.await(key_task)

    assert {:ok, %{api_url: "https://concurrent.example", api_key: @api_key}} =
             load_default(config_root: root)
  end

  @tag :tmp_dir
  test "rejects unsafe configuration directories and invalid API URLs", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    directory = Path.join(root, "taskman")
    File.mkdir_p!(directory)
    File.chmod!(directory, 0o770)

    assert {:error, :invalid_configuration, message} =
             Config.add_host("default", "https://personal.example", config_root: root)

    assert message =~ "directory permissions"

    for url <- [
          "ftp://taskman.example",
          "https://user:pass@taskman.example",
          "https://taskman.example?credential=not-allowed",
          "https://taskman.example#fragment",
          "not a URL"
        ] do
      assert {:error, :invalid_configuration, message} = set_default_url(url, config_root: root)
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
             load_default(config_root: root)

    assert directory_message =~ "directory permissions"

    File.chmod!(Path.dirname(path), 0o700)
    File.chmod!(path, 0o400)

    assert {:error, :invalid_configuration, file_message} = load_default(config_root: root)
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
               load_default(config_root: root)

      assert file_message =~ "chmod 600"
    end

    File.chmod!(path, 0o600)
    File.chmod!(directory, 0o1700)
    assert {:ok, %File.Stat{mode: directory_mode}} = File.stat(directory)

    if (directory_mode &&& 0o7000) != 0 do
      assert {:error, :invalid_configuration, directory_message} =
               load_default(config_root: root)

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
             load_default(config_root: root)

    assert symlink_message =~ "regular file"

    File.rm!(path)
    File.ln!(linked_target, path)

    assert {:error, :invalid_configuration, hardlink_message} =
             load_default(config_root: root)

    assert hardlink_message =~ "hard links"

    File.rm!(path)
    File.write!(path, linked_contents)
    File.chmod!(path, 0o600)

    assert {:error, :invalid_configuration, replacement_message} =
             load_default(
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
  test "accepts only structurally valid Taskman keys and constrained base URLs", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")

    assert :ok = set_default_url("https://[::1]:4001/", config_root: root)
    assert :ok = set_default_url("http://localhost:4000", config_root: root)

    for url <- [
          "https://taskman.example/path",
          "https://taskman.example:0",
          "https://taskman.example:65536",
          "https://user:password@taskman.example",
          "ssh://taskman.example"
        ] do
      assert {:error, :invalid_configuration, _message} = set_default_url(url, config_root: root)
    end

    for key <- [
          "tm_missing",
          "tm_payload_",
          "tm_payload_checksum\n",
          "tm_payload checksum"
        ] do
      assert {:error, :invalid_configuration, _message} =
               Config.set_host_key("default", key, config_root: root)
    end
  end

  @tag :tmp_dir
  test "absent named configuration creates no directory or localhost host", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "absent")

    assert {:ok, %{format: :absent, hosts: %{}, default_host: nil}} =
             Config.load(config_root: root)

    refute File.exists?(root)

    assert :ok = Config.add_host("personal", "https://personal.example", config_root: root)

    assert {:ok,
            %{
              format: :v2,
              default_host: nil,
              hosts: %{"personal" => %{api_url: "https://personal.example", api_key: nil}}
            }} = Config.load(config_root: root)

    assert persisted_hosts(root) == %{
             "version" => 2,
             "hosts" => %{"personal" => %{"api_url" => "https://personal.example"}}
           }

    assert (File.stat!(config_path(root)).mode &&& 0o7777) == 0o600
    assert (File.stat!(Path.dirname(config_path(root))).mode &&& 0o7777) == 0o700
  end

  @tag :tmp_dir
  test "reads preserve legacy bytes and successful no-op mutation upgrades", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")

    path =
      Fixtures.legacy_config!(root, %{
        "api_url" => "https://stored.example",
        "api_key" => @api_key
      })

    bytes = File.read!(path)

    assert {:ok,
            %{
              format: :legacy,
              default_host: "default",
              hosts: %{"default" => %{api_url: "https://stored.example", api_key: @api_key}}
            }} = Config.load(config_root: root)

    assert File.read!(path) == bytes

    assert :ok =
             Config.set_host_url("default", "https://stored.example",
               config_root: root,
               env: %{
                 "TASKMAN_API_URL" => "https://injected.example",
                 "TASKMAN_API_KEY" => "tm_injected_secret",
                 "TASKMAN_HOST" => "injected"
               }
             )

    assert persisted_hosts(root) == %{
             "version" => 2,
             "default_host" => "default",
             "hosts" => %{
               "default" => %{"api_url" => "https://stored.example", "api_key" => @api_key}
             }
           }

    assert {:ok, %{format: :v2}} = Config.load(config_root: root)
  end

  @tag :tmp_dir
  test "failed host mutation preserves bytes and unrelated hosts", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")

    path =
      Fixtures.legacy_config!(root, %{
        "api_url" => "https://legacy.example",
        "api_key" => @api_key
      })

    bytes = File.read!(path)

    failures = [
      fn -> Config.add_host("default", "https://duplicate.example", config_root: root) end,
      fn -> Config.set_host_url("missing", "https://missing.example", config_root: root) end,
      fn -> Config.set_host_key("missing", @api_key, config_root: root) end,
      fn -> Config.remove_host("missing", config_root: root) end,
      fn -> Config.set_default_host("missing", config_root: root) end
    ]

    for mutate <- failures do
      assert {:error, :invalid_invocation, message} = mutate.()
      refute message =~ @api_key
      assert File.read!(path) == bytes
    end

    assert {:error, :invalid_configuration, _} =
             Config.set_host_url("default", "ftp://bad.example", config_root: root)

    assert {:error, :invalid_configuration, _} =
             Config.set_host_key("default", "invalid-secret", config_root: root)

    assert File.read!(path) == bytes

    assert :ok = Config.add_host("work", "https://work.example", config_root: root)
    assert :ok = Config.set_host_key("work", "tm_work_secret", config_root: root)
    bytes = File.read!(path)

    assert {:error, :invalid_configuration, message} =
             Config.set_host_url("default", "https://replacement.example",
               config_root: root,
               rename: fn _, _ -> {:error, :eacces} end
             )

    refute message =~ @api_key
    assert File.read!(path) == bytes
    assert File.ls!(Path.dirname(path)) == ["config.json"]
  end

  @tag :tmp_dir
  test "same-host address changes retain key and default", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{
        "personal" => %{api_url: "https://before.example", api_key: @api_key},
        "work" => %{api_url: "https://work.example", api_key: nil}
      }
    })

    assert :ok = Config.set_host_url("personal", "https://after.example", config_root: root)

    assert persisted_hosts(root) == %{
             "version" => 2,
             "default_host" => "personal",
             "hosts" => %{
               "personal" => %{"api_url" => "https://after.example", "api_key" => @api_key},
               "work" => %{"api_url" => "https://work.example"}
             }
           }

    assert :ok = Config.set_default_host("work", config_root: root)
    assert :ok = Config.set_host_key("work", "tm_work_secret", config_root: root)

    assert {:ok,
            %{
              default_host: "work",
              hosts: %{"personal" => %{api_key: @api_key}, "work" => %{api_key: "tm_work_secret"}}
            }} = Config.load(config_root: root)
  end

  @tag :tmp_dir
  test "removing the default leaves no replacement", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{
        "personal" => %{api_url: "https://personal.example", api_key: @api_key},
        "work" => %{api_url: "https://work.example", api_key: nil}
      }
    })

    assert :ok = Config.remove_host("personal", config_root: root)

    assert persisted_hosts(root) == %{
             "version" => 2,
             "hosts" => %{"work" => %{"api_url" => "https://work.example"}}
           }

    assert :ok = Config.remove_host("work", config_root: root)
    assert persisted_hosts(root) == %{"version" => 2, "hosts" => %{}}
  end

  @tag :tmp_dir
  test "removing another host preserves the selected default", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    assert :ok = Config.add_host("personal", "https://personal.example", config_root: root)
    assert :ok = Config.add_host("work", "https://work.example", config_root: root)
    assert :ok = Config.set_default_host("personal", config_root: root)
    assert :ok = Config.remove_host("work", config_root: root)

    assert persisted_hosts(root) == %{
             "version" => 2,
             "default_host" => "personal",
             "hosts" => %{"personal" => %{"api_url" => "https://personal.example"}}
           }
  end

  @tag :tmp_dir
  test "host loads and mutations refuse every invalid stored connection", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")

    for settings <- [
          %{"api_url" => "ftp://unsafe.example", "api_key" => @api_key},
          %{"api_key" => "bad-secret"},
          %{"version" => 2, "hosts" => %{}, "unexpected" => @api_key}
        ] do
      path = Fixtures.legacy_config!(root, settings)
      bytes = File.read!(path)
      assert {:error, :invalid_configuration, message} = Config.load(config_root: root)
      refute message =~ @api_key

      assert {:error, :invalid_configuration, _} =
               Config.add_host("personal", "https://valid.example", config_root: root)

      assert File.read!(path) == bytes
    end
  end

  @tag :tmp_dir
  test "named host access preserves private path and opened-file protections", %{tmp_dir: tmp_dir} do
    root = Path.join(tmp_dir, "xdg")
    path = Fixtures.legacy_config!(root, %{"api_key" => @api_key})
    File.chmod!(path, 0o644)
    assert {:error, :invalid_configuration, _} = Config.load(config_root: root)

    assert {:error, :invalid_configuration, _} =
             Config.add_host("personal", "https://personal.example", config_root: root)

    File.chmod!(path, 0o600)
    File.chmod!(Path.dirname(path), 0o755)
    assert {:error, :invalid_configuration, _} = Config.load(config_root: root)
    assert {:error, :invalid_configuration, _} = Config.remove_host("default", config_root: root)
    File.chmod!(Path.dirname(path), 0o700)

    target = Path.join(tmp_dir, "linked-config.json")
    File.rename!(path, target)
    File.ln_s!(target, path)
    assert {:error, :invalid_configuration, _} = Config.load(config_root: root)

    assert {:error, :invalid_configuration, _} =
             Config.set_host_key("default", @api_key, config_root: root)

    File.rm!(path)
    File.ln!(target, path)
    assert {:error, :invalid_configuration, _} = Config.load(config_root: root)

    assert {:error, :invalid_configuration, _} =
             Config.set_default_host("default", config_root: root)

    File.rm!(path)
    File.rename!(target, path)

    assert {:error, :invalid_configuration, _} =
             Config.load(
               config_root: root,
               before_open: fn ^path ->
                 File.rename!(path, target)
                 Fixtures.legacy_config!(root, %{"api_url" => "https://replacement.example"})
               end
             )

    assert {:error, :invalid_configuration, _} =
             Config.load(config_root: root, read_file: fn _ -> {:error, :eacces} end)
  end

  @tag :tmp_dir
  test "concurrent independent host edits re-read under the shared config lock", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")

    Fixtures.write_hosts!(root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{
        "personal" => %{api_url: "https://before.example", api_key: @api_key},
        "work" => %{api_url: "https://work.example", api_key: nil}
      }
    })

    supervisor = start_supervised!(Task.Supervisor)
    parent = self()

    first =
      Task.Supervisor.async_nolink(supervisor, fn ->
        Config.set_host_url("personal", "https://after.example",
          config_root: root,
          before_write: fn _ ->
            send(parent, :first_has_read)
            assert_receive :release_first, @coordination_timeout
            :ok
          end
        )
      end)

    assert_receive :first_has_read, @coordination_timeout

    second =
      Task.Supervisor.async_nolink(supervisor, fn ->
        Config.set_host_key("work", "tm_work_secret",
          config_root: root,
          read_file: fn device ->
            contents = IO.binread(device, :eof)
            send(parent, :second_has_read)
            {:ok, contents}
          end
        )
      end)

    assert_receive :second_has_read, @coordination_timeout

    assert {:error, :invalid_configuration, message} =
             Config.set_default_host("work", config_root: root, lock_retry_limit: 0)

    assert message =~ "busy"
    send(first.pid, :release_first)
    assert :ok = Task.await(first)
    assert :ok = Task.await(second)

    assert persisted_hosts(root) == %{
             "version" => 2,
             "default_host" => "personal",
             "hosts" => %{
               "personal" => %{"api_url" => "https://after.example", "api_key" => @api_key},
               "work" => %{"api_url" => "https://work.example", "api_key" => "tm_work_secret"}
             }
           }
  end

  @tag :tmp_dir
  test "post-replacement sync failure reports possible mutation and retains new state", %{
    tmp_dir: tmp_dir
  } do
    root = Path.join(tmp_dir, "xdg")
    Fixtures.legacy_config!(root, %{"api_url" => "https://before.example", "api_key" => @api_key})

    assert {:error, :invalid_configuration, message} =
             Config.set_host_url("default", "https://after.example",
               config_root: root,
               sync_directory: fn _ -> {:error, :eio} end
             )

    assert message =~ "may already have changed"
    assert message =~ "config hosts"
    refute message =~ @api_key

    assert persisted_hosts(root) == %{
             "version" => 2,
             "default_host" => "default",
             "hosts" => %{
               "default" => %{"api_url" => "https://after.example", "api_key" => @api_key}
             }
           }

    assert File.ls!(Path.dirname(config_path(root))) == ["config.json"]
  end

  defp load_default(options) do
    with {:ok, document} <- Config.load(options),
         do: {:ok, Map.get(document.hosts, "default", %{api_url: nil, api_key: nil})}
  end

  defp set_default_url(url, options) do
    with {:ok, document} <- Config.load(options) do
      if Map.has_key?(document.hosts, "default"),
        do: Config.set_host_url("default", url, options),
        else: Config.add_host("default", url, options)
    end
  end

  defp stored_default_url(path) do
    data = Jason.decode!(File.read!(path))
    if Map.has_key?(data, "hosts"), do: data["hosts"]["default"]["api_url"], else: data["api_url"]
  end

  defp persisted_hosts(root), do: root |> config_path() |> File.read!() |> Jason.decode!()

  defp write_config(root, contents) do
    path = config_path(root)
    File.mkdir_p!(Path.dirname(path))
    File.chmod!(Path.dirname(path), 0o700)
    File.write!(path, Jason.encode!(contents))
    File.chmod!(path, 0o600)
  end

  defp config_path(root), do: Path.join([root, "taskman", "config.json"])
end
