defmodule Taskman.Accounts.ApiKey.PersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures

  alias Taskman.Accounts.ApiKey
  alias Taskman.Accounts.ApiKey.Persistence
  alias Taskman.Repo

  test "bulk persistence operations revoke and delete only one user's keys" do
    user = user_fixture()
    other_user = user_fixture()
    active = api_key_fixture(user)
    deleted = api_key_fixture(user)
    untouched = api_key_fixture(other_user)
    previous_revocation = DateTime.add(DateTime.utc_now(), -3_600, :second)

    previously_revoked =
      user
      |> api_key_fixture()
      |> Ecto.Changeset.change(revoked_at: previous_revocation)
      |> Repo.update!()

    revoked_at = DateTime.utc_now()

    assert :ok = Persistence.mark_all_revoked_for_user(user, revoked_at)
    assert Repo.get!(ApiKey, active.id).revoked_at == revoked_at
    assert Repo.get!(ApiKey, deleted.id).revoked_at == revoked_at
    assert Repo.get!(ApiKey, previously_revoked.id).revoked_at == previous_revocation
    assert is_nil(Repo.get!(ApiKey, untouched.id).revoked_at)

    assert :ok = Persistence.delete_all_for_user(user)
    refute Repo.get(ApiKey, active.id)
    refute Repo.get(ApiKey, deleted.id)
    refute Repo.get(ApiKey, previously_revoked.id)
    assert Repo.get(ApiKey, untouched.id)
  end
end
