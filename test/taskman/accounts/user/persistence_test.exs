defmodule Taskman.Accounts.User.PersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures

  alias Taskman.Accounts.User
  alias Taskman.Accounts.User.Persistence

  test "lock reloads the persisted user" do
    %User{} = user = user_fixture()
    stale_user = %User{user | status: :disabled}

    assert {:ok, %User{id: id, status: :active}} = Persistence.lock(stale_user.id)
    assert id == user.id
    assert :error = Persistence.lock(Ecto.UUID.generate())
    assert :error = Persistence.lock(nil)
  end

  test "update_email persists only email identity attributes" do
    user = user_fixture()
    confirmed_at = DateTime.utc_now()

    assert {:ok, updated} =
             Persistence.update_email(user, %{
               email: "replacement@example.com",
               confirmed_at: confirmed_at
             })

    assert to_string(updated.email) == "replacement@example.com"
    assert updated.confirmed_at == confirmed_at
  end
end
