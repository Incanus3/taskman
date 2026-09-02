defmodule Taskman.Accounts.User do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    extensions: [AshAuthentication],
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  alias Taskman.Accounts.{ApiKey, Token}
  alias Taskman.Accounts.User.Status

  postgres do
    table "users"
    repo Taskman.Repo
  end

  actions do
    defaults [:read]

    create :create_pending_user do
      primary? true
      accept [:email]
    end

    create :bootstrap_user do
      public? false
      accept [:admin?, :confirmed_at, :email, :hashed_password, :status]
    end

    read :sign_in_with_api_key do
      argument :api_key, :string do
        allow_nil? false
        sensitive? true
      end

      prepare AshAuthentication.Strategy.ApiKey.SignInPreparation
    end
  end

  attributes do
    uuid_primary_key :id

    attribute :email, :ci_string do
      allow_nil? false
      constraints casing: :lower
      public? true
    end

    attribute :hashed_password, :string do
      allow_nil? true
      sensitive? true
      public? false
    end

    attribute :status, Status do
      allow_nil? false
      default :pending
    end

    attribute :admin?, :boolean do
      allow_nil? false
      default false
    end

    attribute :confirmed_at, :utc_datetime_usec do
      allow_nil? true
    end

    timestamps()
  end

  relationships do
    has_many :api_keys, ApiKey
  end

  identities do
    identity :unique_email, [:email]
  end

  validations do
    validate present(:hashed_password),
      where: [attribute_does_not_equal(:status, :pending)]
  end

  authentication do
    tokens do
      enabled? true
      token_resource Token
      store_all_tokens? true
      require_token_presence_for_authentication? true
      token_lifetime {30, :days}

      signing_secret fn _, _ ->
        Application.fetch_env!(:taskman, :token_signing_secret)
      end
    end

    strategies do
      api_key do
        api_key_relationship :api_keys
      end
    end
  end

  policies do
    bypass AshAuthentication.Checks.AshAuthenticationInteraction do
      authorize_if always()
    end

    bypass [
      action([:create_pending_user, :bootstrap_user]),
      actor_attribute_equals(:accounts_bootstrap?, true)
    ] do
      authorize_if always()
    end

    policy always() do
      forbid_if always()
    end
  end
end
