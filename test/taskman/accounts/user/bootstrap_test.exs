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
end
