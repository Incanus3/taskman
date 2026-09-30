defmodule Taskman.Accounts.User.BootstrapTest do
  use Taskman.DataCase, async: false

  alias Taskman.Accounts

  test "bootstrap creates an active confirmed administrator" do
    assert {:ok, user} =
             Accounts.bootstrap_admin("Administrator@Example.com", "password1")

    assert to_string(user.email) == "administrator@example.com"
    assert user.status == :active
    assert user.admin?
    assert %DateTime{} = user.confirmed_at
  end

  test "bootstrap rejects an existing email without changing the account" do
    assert {:ok, original} = Accounts.bootstrap_admin("duplicate@example.com", "password1")
    assert {:error, _error} = Accounts.bootstrap_admin("duplicate@example.com", "password1")

    assert original.id
  end

  test "bootstrap accepts eight codepoints with seven graphemes" do
    assert {:ok, user} = Accounts.bootstrap_admin("unicode@example.com", "abcdefg\u0301")

    assert user.status == :active
    assert user.admin?

    assert {:ok, signed_in} =
             Accounts.sign_in_with_password(%{
               "email" => "unicode@example.com",
               "password" => "abcdefg\u0301"
             })

    assert signed_in.id == user.id
  end

  test "bootstrap rejects passwords exceeding 128 codepoints" do
    password = String.duplicate("a", 128) <> "\u0301"

    assert {:error, _error} = Accounts.bootstrap_admin("too-long@example.com", password)
  end
end
