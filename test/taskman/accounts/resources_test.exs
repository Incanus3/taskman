defmodule Taskman.Accounts.ResourcesTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures

  alias Taskman.Accounts
  alias Taskman.Accounts.{ApiKey, User}

  test "a pending user has a UUID, normalized email, and safe defaults" do
    pending = pending_user_fixture(email: "User@Example.com")

    assert {:ok, _uuid} = Ecto.UUID.cast(pending.id)
    assert to_string(pending.email) == "user@example.com"
    assert pending.status == :pending
    refute pending.admin?
    assert is_nil(pending.hashed_password)
  end

  test "email uniqueness is case insensitive" do
    pending_user_fixture(email: "User@Example.com")

    assert {:error, _error} =
             Accounts.create_pending_user(%{email: "user@example.com"},
               actor: %{accounts_bootstrap?: true}
             )
  end

  test "only pending users may omit a password hash" do
    assert {:error, _error} =
             Accounts.bootstrap_user(
               %{email: "active-without-a-password@example.com", status: :active},
               actor: %{accounts_bootstrap?: true}
             )
  end

  test "credential fields are sensitive and non-public" do
    assert Ash.Resource.Info.attribute(User, :hashed_password).sensitive?
    refute Ash.Resource.Info.attribute(User, :hashed_password).public?

    assert Ash.Resource.Info.attribute(ApiKey, :api_key_hash).sensitive?
    refute Ash.Resource.Info.attribute(ApiKey, :api_key_hash).public?
  end

  test "the user resource exposes the API-key sign-in action" do
    assert Ash.Resource.Info.action(User, :sign_in_with_api_key)
  end

  test "API key records persist only the hash" do
    api_key = user_fixture() |> api_key_fixture()

    refute Map.has_key?(Map.from_struct(api_key), :plaintext_api_key)
    assert is_binary(api_key.api_key_hash)
  end
end
