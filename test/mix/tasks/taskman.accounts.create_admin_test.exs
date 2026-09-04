defmodule Mix.Tasks.Taskman.Accounts.CreateAdminTest do
  use Taskman.DataCase, async: false

  import ExUnit.CaptureIO

  alias Taskman.{Accounts, FakeTerminal, Repo}
  alias Taskman.Accounts.User

  setup do
    previous_terminal = Application.get_env(:taskman, :terminal)

    Application.put_env(:taskman, :terminal, FakeTerminal)

    on_exit(fn ->
      if previous_terminal do
        Application.put_env(:taskman, :terminal, previous_terminal)
      else
        Application.delete_env(:taskman, :terminal)
      end
    end)
  end

  test "retries invalid credentials and creates an administrator" do
    email = "mix-admin-#{System.unique_integer([:positive])}@example.com"
    password = "mix-password-#{System.unique_integer([:positive])}"

    FakeTerminal.set_responses(
      ["not-an-email", email],
      [password, "different-password", password, password]
    )

    output = capture_io(fn -> Mix.Tasks.Taskman.Accounts.CreateAdmin.run([]) end)

    assert output =~ "Administrator created."
    refute output =~ password

    assert [
             {:visible, "Email: "},
             {:visible, "Email: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()

    assert %User{status: :active, admin?: true, confirmed_at: %DateTime{}} =
             Repo.get_by!(User, email: email)
  end

  test "fails safely without echoing a password when the account cannot be created" do
    email = "existing@example.com"
    password = "existing-password"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    FakeTerminal.set_responses([email], [password, password])

    output =
      capture_io(fn ->
        assert_raise Mix.Error, ~r/Unable to create administrator/, fn ->
          Mix.Tasks.Taskman.Accounts.CreateAdmin.run([])
        end
      end)

    refute output =~ password

    assert [
             {:visible, "Email: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()
  end

  test "fails safely when terminal input is unavailable without requesting secrets" do
    FakeTerminal.set_responses([{:error, :simulated_terminal_failure}], [])

    output =
      capture_io(fn ->
        assert_raise Mix.Error, ~r/Unable to create administrator/, fn ->
          Mix.Tasks.Taskman.Accounts.CreateAdmin.run([])
        end
      end)

    refute output =~ "simulated_terminal_failure"
    assert [{:visible, "Email: "}] = FakeTerminal.prompts()
  end

  test "fails safely without echoing the password when persistence raises" do
    email = "unavailable-repo@example.com"
    password = "unavailable-repo-password"
    FakeTerminal.set_responses([email], [password, password])

    assert :ok = Supervisor.terminate_child(Taskman.Supervisor, Repo)

    on_exit(fn ->
      assert {:ok, _pid} = Supervisor.restart_child(Taskman.Supervisor, Repo)
    end)

    output =
      capture_io(fn ->
        assert_raise Mix.Error, ~r/Unable to create administrator/, fn ->
          Mix.Tasks.Taskman.Accounts.CreateAdmin.run([])
        end
      end)

    refute output =~ password
  end
end
