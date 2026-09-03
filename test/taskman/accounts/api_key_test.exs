defmodule Taskman.Accounts.ApiKeyTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures

  require Ash.Query

  alias AshAuthentication.Errors.AuthenticationFailed
  alias Taskman.Accounts
  alias Taskman.Accounts.ApiKey
  alias Taskman.Repo

  @api_key_lifetime_seconds 365 * 86_400

  test "creates a hash-only tm key with one-time plaintext and one-year expiry" do
    user = user_fixture()
    before = DateTime.utc_now()

    assert {:ok, %{api_key: api_key, plaintext: plaintext}} =
             Accounts.create_api_key(user, %{
               name: "Automation",
               expires_at: DateTime.add(before, @api_key_lifetime_seconds, :second)
             })

    after_creation = DateTime.utc_now()

    assert Regex.match?(~r/\Atm_[A-Za-z0-9]+_[A-Za-z0-9]+\z/, plaintext)
    assert api_key.name == "Automation"
    assert DateTime.diff(api_key.expires_at, before, :second) in 31_535_999..31_536_001
    assert DateTime.diff(api_key.expires_at, after_creation, :second) in 31_535_998..31_536_001
    refute Map.has_key?(Map.from_struct(api_key), :plaintext)
    refute Map.has_key?(Map.from_struct(api_key), :plaintext_api_key)

    assert %ApiKey{} = reloaded = Repo.get!(ApiKey, api_key.id)
    assert reloaded.api_key_hash == api_key.api_key_hash
    refute Map.has_key?(Map.from_struct(reloaded), :plaintext)
    refute Map.has_key?(Map.from_struct(reloaded), :plaintext_api_key)
    assert {:ok, %{id: id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert id == user.id
  end

  test "accepts a shorter expiration but rejects missing, past, and over-one-year expirations" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{api_key: key}} =
             Accounts.create_api_key(user, %{
               name: "Short-lived",
               expires_at: DateTime.add(now, 86_400, :second)
             })

    assert DateTime.diff(key.expires_at, now, :second) in 86_399..86_401

    assert {:ok, %{api_key: one_year_key}} =
             Accounts.create_api_key(user, %{
               name: "One year",
               expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
             })

    assert DateTime.diff(one_year_key.expires_at, now, :second) in 31_535_999..31_536_001
    assert {:error, _} = Accounts.create_api_key(user, %{name: "Omitted"})
    assert {:error, _} = Accounts.create_api_key(user, %{name: "Missing", expires_at: nil})

    assert {:error, _} =
             Accounts.create_api_key(user, %{
               name: "Past",
               expires_at: DateTime.add(now, -1, :second)
             })

    assert {:error, _} =
             Accounts.create_api_key(user, %{
               name: "Too long",
               expires_at: DateTime.add(now, 31_536_001, :second)
             })
  end

  test "revoked and expired keys cannot authenticate" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{api_key: revoked, plaintext: revoked_plaintext}} =
             Accounts.create_api_key(user, %{
               name: "Revoked",
               expires_at: DateTime.add(now, 86_400, :second)
             })

    assert :ok = Accounts.revoke_api_key(user, revoked.id)

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_api_key(%{api_key: revoked_plaintext})

    assert {:ok, %{plaintext: expired_plaintext, api_key: expired}} =
             Accounts.create_api_key(user, %{
               name: "Expired",
               expires_at: DateTime.add(now, 86_400, :second)
             })

    assert {:ok, _expired} =
             expired
             |> Ecto.Changeset.change(expires_at: DateTime.add(now, -1, :second))
             |> Repo.update()

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_api_key(%{api_key: expired_plaintext})
  end

  test "pending and disabled users cannot create or authenticate keys" do
    pending = pending_user_fixture()
    disabled = user_fixture(status: :disabled)
    unconfirmed = user_fixture(status: :active, confirmed_at: nil)

    {:ok, unconfirmed} =
      unconfirmed
      |> Ecto.Changeset.change(confirmed_at: nil)
      |> Repo.update()

    expires_at = DateTime.add(DateTime.utc_now(), 86_400, :second)

    assert {:error, _} =
             Accounts.create_api_key(pending, %{name: "Pending", expires_at: expires_at})

    assert {:error, _} =
             Accounts.create_api_key(disabled, %{name: "Disabled", expires_at: expires_at})

    assert {:error, _} =
             Accounts.create_api_key(unconfirmed, %{name: "Unconfirmed", expires_at: expires_at})
  end

  test "key reads and revocations are scoped to the acting owner" do
    owner = user_fixture()
    other = user_fixture()

    assert {:ok, %{api_key: key}} =
             Accounts.create_api_key(owner, %{
               name: "Owner key",
               expires_at: DateTime.add(DateTime.utc_now(), 86_400, :second)
             })

    assert {:ok, [listed_key]} = Accounts.list_api_keys(owner)
    assert listed_key.id == key.id

    assert {:ok, []} = Accounts.list_api_keys(other)

    assert {:ok, fetched_key} = read_key(owner, key.id)
    assert fetched_key.id == key.id
    assert {:ok, nil} = read_key(other, key.id)
    assert {:error, :not_found} = Accounts.revoke_api_key(other, key.id)
    assert :ok = Accounts.revoke_api_key(owner, key.id)
  end

  defp read_key(actor, id) do
    ApiKey
    |> Ash.Query.for_read(:get_for_user, %{}, actor: actor, domain: Accounts)
    |> Ash.Query.filter(id: id)
    |> Ash.read_one(actor: actor, domain: Accounts)
  end
end
