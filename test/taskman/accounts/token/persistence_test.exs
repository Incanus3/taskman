defmodule Taskman.Accounts.Token.PersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures

  alias Taskman.Accounts.Token
  alias Taskman.Accounts.Token.Persistence
  alias Taskman.Repo

  test "delete_for_subject deletes only the requested purposes and returns their identities" do
    user = user_fixture()
    other_user = user_fixture()

    setup = token_fixture(user, "setup")
    email_change = token_fixture(user, "email_change")
    other_setup = token_fixture(other_user, "setup")

    assert {:ok, [{jti, "setup"}]} = Persistence.delete_for_subject(user, ["setup"])
    assert jti == setup.jti

    refute Repo.get_by(Token, jti: setup.jti, purpose: setup.purpose)
    assert Repo.get_by(Token, jti: email_change.jti, purpose: email_change.purpose)
    assert Repo.get_by(Token, jti: other_setup.jti, purpose: other_setup.purpose)

    assert {:ok, [{email_change_jti, "email_change"}]} =
             Persistence.delete_for_subject(user, nil)

    assert email_change_jti == email_change.jti
    refute Repo.get_by(Token, jti: email_change.jti, purpose: email_change.purpose)
    assert Repo.get_by(Token, jti: other_setup.jti, purpose: other_setup.purpose)
  end

  defp token_fixture(user, purpose) do
    now = DateTime.utc_now()

    Repo.insert!(%Token{
      jti: Ecto.UUID.generate(),
      subject: AshAuthentication.user_to_subject(user),
      purpose: purpose,
      expires_at: now |> DateTime.add(3_600, :second) |> DateTime.truncate(:second),
      created_at: now,
      updated_at: now
    })
  end
end
