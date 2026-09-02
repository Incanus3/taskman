defmodule Taskman.Accounts.BootstrapTest do
  use Taskman.DataCase, async: false

  alias Taskman.Accounts

  test "bootstrap creates an active confirmed administrator" do
    assert {:ok, user} =
             Accounts.bootstrap_admin(%{
               email: "Administrator@Example.com",
               password: "password1",
               password_confirmation: "password1"
             })

    assert to_string(user.email) == "administrator@example.com"
    assert user.status == :active
    assert user.admin?
    assert %DateTime{} = user.confirmed_at
  end

  test "bootstrap rejects an existing email without changing the account" do
    attributes = %{
      email: "duplicate@example.com",
      password: "password1",
      password_confirmation: "password1"
    }

    assert {:ok, original} = Accounts.bootstrap_admin(attributes)
    assert {:error, _error} = Accounts.bootstrap_admin(attributes)

    assert original.id
  end
end
