defmodule Taskman.Accounts.AdministrationTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures

  alias Taskman.Accounts.Administration
  alias Taskman.Accounts.User

  test "lock_active_administrator reloads authority from persisted state" do
    %User{} = administrator = admin_fixture()
    stale_actor = %User{administrator | status: :disabled, admin?: false}

    assert {:ok, %User{id: id, status: :active, admin?: true}} =
             Administration.lock_active_administrator(stale_actor)

    assert id == administrator.id

    ordinary_user = user_fixture()
    assert :error = Administration.lock_active_administrator(ordinary_user)
    assert :error = Administration.lock_active_administrator(nil)
  end
end
