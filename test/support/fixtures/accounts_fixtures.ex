defmodule Taskman.AccountsFixtures do
  alias Taskman.Accounts

  @bootstrap_actor %{accounts_bootstrap?: true}

  def pending_user_fixture(attrs \\ %{}) do
    unique = System.unique_integer([:positive])

    attrs =
      Map.merge(
        %{email: "pending-#{unique}@example.com"},
        Map.new(attrs)
      )

    {:ok, user} = Accounts.create_pending_user(attrs, actor: @bootstrap_actor)
    user
  end

  def user_fixture(attrs \\ %{}) do
    unique = System.unique_integer([:positive])

    attrs =
      Map.merge(
        %{
          email: "user-#{unique}@example.com",
          hashed_password: "fixture-password-hash",
          status: :active,
          confirmed_at: DateTime.utc_now()
        },
        Map.new(attrs)
      )

    {:ok, user} = Accounts.bootstrap_user(attrs, actor: @bootstrap_actor)
    user
  end

  def admin_fixture(attrs \\ %{}) do
    user_fixture(Map.put(Map.new(attrs), :admin?, true))
  end

  def api_key_fixture(user, attrs \\ %{}) do
    unique = System.unique_integer([:positive])

    attrs =
      Map.merge(
        %{
          user_id: user.id,
          name: "Fixture key #{unique}",
          api_key_hash: :crypto.hash(:sha256, "fixture-api-key-#{unique}"),
          expires_at: DateTime.add(DateTime.utc_now(), 86_400, :second)
        },
        Map.new(attrs)
      )

    {:ok, api_key} = Accounts.create_api_key_record(attrs, actor: @bootstrap_actor)
    api_key
  end
end
