defmodule Taskman.Accounts.AdministrationTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures

  alias Taskman.Accounts
  alias Taskman.Accounts.Administration
  alias Taskman.Accounts.User

  test "an active administrator can look up a user through Accounts" do
    administrator = admin_fixture()
    user = user_fixture()

    assert {:ok, %User{} = inspected_user} = Accounts.get_admin_user(administrator, user.id)
    assert inspected_user.id == user.id
    assert inspected_user.email == user.email
    assert inspected_user.status == user.status
    assert inspected_user.admin? == user.admin?
    assert inspected_user.confirmed_at == user.confirmed_at
    assert inspected_user.inserted_at == user.inserted_at
    assert inspected_user.updated_at == user.updated_at
    assert %Ash.NotLoaded{} = inspected_user.hashed_password
    assert %Ash.NotLoaded{} = inspected_user.email_change_confirmed_at
    assert %Ash.NotLoaded{} = inspected_user.api_keys
    assert %Ash.NotLoaded{} = inspected_user.valid_api_keys
  end

  test "an ordinary user cannot use the administrative lookup" do
    ordinary_user = user_fixture()
    target = user_fixture()

    assert {:error, _reason} = Accounts.get_admin_user(ordinary_user, target.id)
  end

  test "the administrative lookup reports a missing user" do
    administrator = admin_fixture()

    assert {:error, :not_found} =
             Accounts.get_admin_user(administrator, Ecto.UUID.generate())
  end

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
