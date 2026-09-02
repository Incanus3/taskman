defmodule Taskman.Accounts.ApiKey do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  alias Taskman.Accounts.User

  postgres do
    table "api_keys"
    repo Taskman.Repo
  end

  actions do
    defaults [:read]

    create :create_for_bootstrap do
      primary? true
      public? false
      accept [:api_key_hash, :expires_at, :name, :user_id]
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

    policy always() do
      forbid_if always()
    end
  end
end
