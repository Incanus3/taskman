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
             Accounts.create_api_key(
               user,
               %{
                 name: "Automation",
                 expires_at: DateTime.add(before, @api_key_lifetime_seconds, :second)
               },
               now: before
             )

    after_creation = DateTime.utc_now()

    assert Regex.match?(~r/\Atm_[A-Za-z0-9]+_[A-Za-z0-9]+\z/, plaintext)
    assert api_key.name == "Automation"
    assert DateTime.diff(api_key.expires_at, before, :second) in 31_535_999..31_536_001
    assert DateTime.diff(api_key.expires_at, after_creation, :second) in 31_535_998..31_536_001
    refute Map.has_key?(Map.from_struct(api_key), :plaintext)
    refute Map.has_key?(Map.from_struct(api_key), :plaintext_api_key)

    assert %ApiKey{} = reloaded = Repo.get!(ApiKey, api_key.id)
    assert reloaded.api_key_hash == api_key.api_key_hash
    assert reloaded.api_key_hash == :crypto.hash(:sha256, plaintext)
    refute Map.has_key?(Map.from_struct(reloaded), :plaintext)
    refute Map.has_key?(Map.from_struct(reloaded), :plaintext_api_key)
    assert {:ok, %{id: id}} = Accounts.sign_in_with_api_key(%{api_key: plaintext})
    assert id == user.id
  end

  test "rejects non-canonical textual mutations of an otherwise shaped key" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{plaintext: plaintext}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Canonical",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    [prefix, middle, checksum] = String.split(plaintext, "_")
    non_canonical_middle = prefix <> "_a" <> middle <> "_" <> checksum

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_api_key(%{api_key: non_canonical_middle})

    non_canonical_checksum = prefix <> "_" <> middle <> "_a" <> checksum

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_api_key(%{api_key: non_canonical_checksum})
  end

  test "accepts a shorter expiration but rejects missing, past, and over-one-year expirations" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{api_key: key}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Short-lived",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    assert DateTime.diff(key.expires_at, now, :second) in 86_399..86_401

    assert {:ok, %{api_key: one_year_key}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "One year",
                 expires_at: DateTime.add(now, @api_key_lifetime_seconds, :second)
               },
               now: now
             )

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

  test "checks expiration boundaries at full microsecond precision" do
    user = user_fixture()
    now = DateTime.utc_now() |> DateTime.truncate(:microsecond)
    minimum = DateTime.add(now, @api_key_lifetime_seconds - 364 * 86_400, :second)
    maximum = DateTime.add(now, @api_key_lifetime_seconds, :second)

    assert {:ok, _} =
             Accounts.create_api_key(
               user,
               %{name: "Minimum exact", expires_at: minimum},
               now: now
             )

    assert {:ok, _} =
             Accounts.create_api_key(
               user,
               %{name: "Maximum exact", expires_at: maximum},
               now: now
             )

    assert {:error, _} =
             Accounts.create_api_key(
               user,
               %{name: "Minimum short", expires_at: DateTime.add(minimum, -1, :microsecond)},
               now: now
             )

    assert {:error, _} =
             Accounts.create_api_key(
               user,
               %{name: "Maximum long", expires_at: DateTime.add(maximum, 1, :microsecond)},
               now: now
             )
  end

  test "revoked and expired keys cannot authenticate" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{api_key: revoked, plaintext: revoked_plaintext}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Revoked",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    assert :ok = Accounts.revoke_api_key(user, revoked.id)

    assert {:error, %AuthenticationFailed{}} =
             Accounts.sign_in_with_api_key(%{api_key: revoked_plaintext})

    assert {:ok, %{plaintext: expired_plaintext, api_key: expired}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Expired",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

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
    now = DateTime.utc_now()

    assert {:ok, %{api_key: key}} =
             Accounts.create_api_key(
               owner,
               %{
                 name: "Owner key",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    assert {:ok, [listed_key]} = Accounts.list_api_keys(owner)
    assert listed_key.id == key.id

    assert {:ok, []} = Accounts.list_api_keys(other)

    assert {:ok, fetched_key} = read_key(owner, key.id)
    assert fetched_key.id == key.id
    assert {:ok, nil} = read_key(other, key.id)
    assert {:error, :not_found} = Accounts.revoke_api_key(other, key.id)
    assert :ok = Accounts.revoke_api_key(owner, key.id)
  end

  test "create, list, and revoke reload the actor eligibility from persistence" do
    user = user_fixture()
    now = DateTime.utc_now()

    assert {:ok, %{api_key: key}} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Stale actor",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    {:ok, _disabled} =
      user
      |> Ecto.Changeset.change(status: :disabled)
      |> Repo.update()

    assert {:error, :authentication_required} =
             Accounts.create_api_key(
               user,
               %{
                 name: "Should fail",
                 expires_at: DateTime.add(now, 86_400, :second)
               },
               now: now
             )

    assert {:error, :authentication_required} = Accounts.list_api_keys(user)
    assert {:error, :authentication_required} = Accounts.revoke_api_key(user, key.id)

    unconfirmed = user_fixture()
    unconfirmed_now = DateTime.utc_now()

    assert {:ok, %{api_key: unconfirmed_key}} =
             Accounts.create_api_key(
               unconfirmed,
               %{
                 name: "Unconfirmed stale actor",
                 expires_at: DateTime.add(unconfirmed_now, 86_400, :second)
               },
               now: unconfirmed_now
             )

    {:ok, _unconfirmed} =
      unconfirmed
      |> Ecto.Changeset.change(confirmed_at: nil)
      |> Repo.update()

    assert {:error, :authentication_required} = Accounts.list_api_keys(unconfirmed)

    assert {:error, :authentication_required} =
             Accounts.revoke_api_key(unconfirmed, unconfirmed_key.id)

    deleted = user_fixture()
    assert {:ok, _deleted} = Repo.delete(deleted)

    assert {:error, :authentication_required} =
             Accounts.create_api_key(deleted, %{
               name: "Deleted",
               expires_at: DateTime.add(DateTime.utc_now(), 86_400, :second)
             })
  end

  defp read_key(actor, id) do
    ApiKey
    |> Ash.Query.for_read(:get_for_user, %{}, actor: actor, domain: Accounts)
    |> Ash.Query.filter(id: id)
    |> Ash.read_one(actor: actor, domain: Accounts)
  end
end
