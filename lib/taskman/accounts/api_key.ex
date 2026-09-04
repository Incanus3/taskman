defmodule Taskman.Accounts.ApiKey do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  alias Taskman.Accounts.ApiKey.Changes.{Generate, GuardIssuance}
  alias Taskman.Accounts.ApiKey.Persistence
  alias Taskman.Accounts.User

  postgres do
    table "api_keys"
    repo Taskman.Repo
  end

  actions do
    defaults [:read]

    read :list_for_user do
      filter expr(user_id == ^actor(:id))
    end

    read :get_for_user do
      get? true
      filter expr(user_id == ^actor(:id))
    end

    read :for_authentication do
      get? true
      public? false
      filter expr(is_nil(revoked_at) and expires_at > now())
    end

    create :create_for_bootstrap do
      primary? true
      public? false
      accept [:api_key_hash, :expires_at, :name, :user_id]
    end

    create :create_for_user do
      public? false
      accept [:expires_at, :name, :user_id]

      change GuardIssuance
      change {Generate, prefix: :tm, hash: :api_key_hash}
    end

    update :revoke do
      public? false
      require_atomic? false
      accept []
      change set_attribute(:revoked_at, &DateTime.utc_now/0)
    end
  end

  attributes do
    uuid_primary_key :id

    attribute :name, :string do
      allow_nil? false
    end

    attribute :api_key_hash, :binary do
      allow_nil? false
      sensitive? true
      public? false
    end

    attribute :expires_at, :utc_datetime_usec do
      allow_nil? false
    end

    attribute :revoked_at, :utc_datetime_usec do
      allow_nil? true
    end

    timestamps()
  end

  relationships do
    belongs_to :user, User do
      allow_nil? false
      attribute_public? false
    end
  end

  identities do
    identity :unique_api_key_hash, [:api_key_hash]
  end

  policies do
    bypass AshAuthentication.Checks.AshAuthenticationInteraction do
      authorize_if always()
    end

    bypass [
      action(:create_for_bootstrap),
      actor_attribute_equals(:accounts_bootstrap?, true)
    ] do
      authorize_if always()
    end

    bypass [
      action(:create_for_user),
      actor_attribute_equals(:status, :active),
      expr(not is_nil(^actor(:confirmed_at)))
    ] do
      authorize_if expr(user_id == ^actor(:id))
    end

    bypass [
      action([:list_for_user, :get_for_user]),
      actor_attribute_equals(:status, :active),
      expr(not is_nil(^actor(:confirmed_at)))
    ] do
      authorize_if expr(user_id == ^actor(:id))
    end

    bypass [
      action(:revoke),
      actor_attribute_equals(:status, :active),
      expr(not is_nil(^actor(:confirmed_at)))
    ] do
      authorize_if expr(user_id == ^actor(:id))
    end

    policy [
      action([:create_for_user, :list_for_user, :get_for_user, :revoke]),
      actor_attribute_equals(:confirmed_at, nil)
    ] do
      forbid_if always()
    end

    policy always() do
      forbid_if always()
    end
  end

  @doc false
  @spec revoke_all_for_user(User.t()) :: :ok | {:error, :api_key_revocation_failed}
  def revoke_all_for_user(%User{} = user) do
    Persistence.mark_all_revoked_for_user(user, DateTime.utc_now())
  end
end
