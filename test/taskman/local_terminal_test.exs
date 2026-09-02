defmodule Taskman.LocalTerminalTest do
  use ExUnit.Case, async: false

  alias Taskman.LocalTerminal

  test "reads visible input from an injected IO device" do
    {:ok, device} = StringIO.open("admin@example.com\n")

    assert "admin@example.com" == LocalTerminal.prompt("Email: ", device)
  end

  test "reads secret input from an injected IO device without writing the secret" do
    {:ok, device} = StringIO.open("secret-password\n")

    assert "secret-password" == LocalTerminal.prompt_secret("Password: ", device)

    assert {"", output} = StringIO.contents(device)
    assert output == "Password: "
    refute output =~ "secret-password"
  end

  test "returns a safe terminal error when visible input is unavailable" do
    {:ok, device} = StringIO.open("")

    assert {:error, :input_unavailable} == LocalTerminal.prompt("Email: ", device)
  end

  test "returns a safe terminal error when the visible input device rejects the request" do
    {:ok, device} = StringIO.open("")
    {:ok, _contents} = StringIO.close(device)

    assert {:error, :input_unavailable} == LocalTerminal.prompt("Email: ", device)
  end

  test "returns a safe terminal error when password input is unavailable" do
    {:ok, device} = StringIO.open("")

    assert {:error, :input_unavailable} == LocalTerminal.prompt_secret("Password: ", device)
  end

  test "returns a safe terminal error when the password device rejects the request" do
    assert {:error, :input_unavailable} == LocalTerminal.prompt_secret("", :standard_error)
  end
end
