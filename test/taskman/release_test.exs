defmodule Taskman.ReleaseTest do
  use Taskman.DataCase, async: false

  import ExUnit.CaptureIO

  alias Taskman.{FakeTerminal, Release, Repo}
  alias Taskman.Accounts.User

  test "migrate loads the application and runs every configured repo" do
    assert_release_function(:migrate, 1)

    assert :ok =
             Release.migrate(
               app_loader: fn :taskman ->
                 send(self(), :application_loaded)
                 :ok
               end,
               repo_provider: fn :taskman ->
                 send(self(), :repos_loaded)
                 [FirstRepo, SecondRepo]
               end,
               migrator: fn repo ->
                 send(self(), {:migrated, repo})
                 :ok
               end
             )

    assert_received :application_loaded
    assert_received :repos_loaded
    assert_received {:migrated, FirstRepo}
    assert_received {:migrated, SecondRepo}
  end

  test "migrate stops when a configured repo migration fails" do
    assert_release_function(:migrate, 1)

    assert {:error, {:migration_failed, SecondRepo, :unavailable}} =
             Release.migrate(
               app_loader: fn :taskman -> :ok end,
               repo_provider: fn :taskman -> [FirstRepo, SecondRepo] end,
               migrator: fn
                 FirstRepo -> :ok
                 SecondRepo -> {:error, :unavailable}
               end
             )
  end

  test "create_admin uses the terminal bootstrap boundary without echoing the password" do
    assert_release_function(:create_admin, 1)

    email = "release-admin-#{System.unique_integer([:positive])}@example.com"
    password = "release-password-#{System.unique_integer([:positive])}"
    FakeTerminal.set_responses([email], [password, password])

    output =
      capture_io(fn ->
        assert :ok = Release.create_admin(FakeTerminal)
      end)

    assert output =~ "Administrator created."
    refute output =~ password

    assert [
             {:visible, "Email: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()

    assert %User{status: :active, admin?: true, confirmed_at: %DateTime{}} =
             Repo.get_by!(User, email: email)
  end

  test "create_admin fails without echoing a rejected password" do
    assert_release_function(:create_admin, 1)

    password = "short7!"
    FakeTerminal.set_responses(["release-invalid@example.com"], [password, password])

    output =
      capture_io(fn ->
        assert_raise RuntimeError, "Unable to create administrator.", fn ->
          Release.create_admin(FakeTerminal)
        end
      end)

    refute output =~ password

    assert [
             {:visible, "Email: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()
  end

  test "only the server overlay enables Phoenix endpoint startup" do
    with_release_overlay_bin(fn bin ->
      assert {"server=true args=start\n", 0} = run_overlay(bin, "server")

      assert {"server=unset args=eval Taskman.Release.migrate\n", 0} =
               run_overlay(bin, "migrate")

      assert {"server=unset args=eval Taskman.Release.create_admin\n", 0} =
               run_overlay(bin, "create-admin")
    end)
  end

  defp assert_release_function(function, arity) do
    assert Code.ensure_loaded?(Release), "Taskman.Release must be available in a release"

    assert function_exported?(Release, function, arity),
           "Taskman.Release.#{function}/#{arity} must be available in a release"
  end

  defp with_release_overlay_bin(fun) do
    root = Path.join(System.tmp_dir!(), "taskman-release-overlays-#{System.unique_integer()}")
    bin = Path.join(root, "bin")

    File.mkdir_p!(bin)

    for command <- ["server", "migrate", "create-admin"] do
      File.cp!(Path.join(["rel", "overlays", "bin", command]), Path.join(bin, command))
      File.chmod!(Path.join(bin, command), 0o755)
    end

    release_runner = Path.join(bin, "taskman")

    File.write!(
      release_runner,
      """
      #!/bin/sh
      if [ "${PHX_SERVER+x}" = x ]; then
        printf 'server=%s args=%s\\n' "$PHX_SERVER" "$*"
      else
        printf 'server=unset args=%s\\n' "$*"
      fi
      """
    )

    File.chmod!(release_runner, 0o755)

    try do
      fun.(bin)
    after
      File.rm_rf!(root)
    end
  end

  defp run_overlay(bin, command) do
    System.cmd(Path.join(bin, command), [], env: [{"PHX_SERVER", nil}])
  end
end
