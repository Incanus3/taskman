defmodule Taskman.CredentialPromptsTest do
  use ExUnit.Case, async: true

  alias Taskman.{CredentialPrompts, FakeTerminal}

  test "prompt_for_email retries until the email format is valid" do
    FakeTerminal.set_responses(["not-an-email", " admin@example.com "], [])

    assert {:ok, "admin@example.com"} = CredentialPrompts.prompt_for_email(FakeTerminal)

    assert [
             {:visible, "Email: "},
             {:visible, "Email: "}
           ] = FakeTerminal.prompts()
  end

  test "prompt_for_email stops when terminal input is unavailable" do
    FakeTerminal.set_responses([{:error, :input_unavailable}], [])

    assert {:error, :input_unavailable} = CredentialPrompts.prompt_for_email(FakeTerminal)
    assert [{:visible, "Email: "}] = FakeTerminal.prompts()
  end

  test "prompt_for_password retries invalid lengths and mismatched confirmations" do
    FakeTerminal.set_responses(
      [],
      [
        "short",
        "short",
        "long-enough-password",
        "different-password",
        "accepted-password",
        "accepted-password"
      ]
    )

    assert {:ok, "accepted-password"} = CredentialPrompts.prompt_for_password(FakeTerminal)

    assert [
             {:secret, "Password: "},
             {:secret, "Confirm password: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()
  end

  test "prompt_for_password stops without requesting confirmation when input is unavailable" do
    FakeTerminal.set_responses([], [{:error, :input_unavailable}])

    assert {:error, :input_unavailable} = CredentialPrompts.prompt_for_password(FakeTerminal)
    assert [{:secret, "Password: "}] = FakeTerminal.prompts()
  end

  test "prompt_for_password accepts eight codepoints with seven graphemes" do
    password = "abcdefg\u0301"
    FakeTerminal.set_responses([], [password, password])

    assert {:ok, ^password} = CredentialPrompts.prompt_for_password(FakeTerminal)
    assert [{:secret, "Password: "}, {:secret, "Confirm password: "}] = FakeTerminal.prompts()
  end

  test "prompt_for_password retries passwords exceeding 128 codepoints" do
    password = String.duplicate("a", 128) <> "\u0301"
    FakeTerminal.set_responses([], [password, password, "accepted-password", "accepted-password"])

    assert {:ok, "accepted-password"} = CredentialPrompts.prompt_for_password(FakeTerminal)

    assert [
             {:secret, "Password: "},
             {:secret, "Confirm password: "},
             {:secret, "Password: "},
             {:secret, "Confirm password: "}
           ] = FakeTerminal.prompts()
  end
end
