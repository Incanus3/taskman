defmodule Taskman.Accounts.User.PoliciesTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures

  alias Taskman.Accounts

  test "Accounts creation is denied without an actor" do
    assert {:error, _error} =
             Accounts.create_pending_user(%{email: "no-actor@example.com"})
  end

  test "the named bootstrap authority is sufficient for fixture setup" do
    user = pending_user_fixture()

    assert user.status == :pending
  end
end
