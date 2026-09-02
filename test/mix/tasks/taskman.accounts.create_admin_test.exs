defmodule Mix.Tasks.Taskman.Accounts.CreateAdminTest do
  use Taskman.DataCase, async: false

  import ExUnit.CaptureIO

  alias Taskman.FakeTerminal

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

  test "fails safely without echoing passwords supplied through the injected terminal" do
    password = "short7!"
    FakeTerminal.set_responses(["invalid@example.com"], [password, password])

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
end
