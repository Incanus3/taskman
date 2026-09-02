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

    create :bootstrap_admin do
      public? false
      accept [:email]

      argument :password, :string do
        allow_nil? false
        constraints min_length: 8, max_length: 128
        sensitive? true
      end

      argument :password_confirmation, :string do
        allow_nil? false
        sensitive? true
      end

      validate {AshAuthentication.Strategy.Password.PasswordConfirmationValidation,
                strategy_name: :password}

      change set_attribute(:admin?, true)
      change set_attribute(:status, :active)
      change set_attribute(:confirmed_at, &DateTime.utc_now/0)
      change {AshAuthentication.Strategy.Password.HashPasswordChange, strategy_name: :password}
    end

    update :change_password do
      public? false
      require_atomic? false
      accept []

      argument :current_password, :string do
        allow_nil? false
        sensitive? true
      end

      argument :password, :string do
        allow_nil? false
        constraints min_length: 8, max_length: 128
        sensitive? true
      end

      argument :password_confirmation, :string do
        allow_nil? false
        sensitive? true
      end

      validate {AshAuthentication.Strategy.Password.PasswordConfirmationValidation,
                strategy_name: :password}

      validate {AshAuthentication.Strategy.Password.PasswordValidation,
                strategy_name: :password, password_argument: :current_password}

      change {AshAuthentication.Strategy.Password.HashPasswordChange, strategy_name: :password}
    end

    update :reset_password do
      public? false
      require_atomic? false
      accept []

      argument :reset_token, :string do
        allow_nil? false
        sensitive? true
      end

      argument :password, :string do
        allow_nil? false
        constraints min_length: 8, max_length: 128
        sensitive? true
      end

      argument :password_confirmation, :string do
        allow_nil? false
        sensitive? true
      end

      validate AshAuthentication.Strategy.Password.ResetTokenValidation

      validate {AshAuthentication.Strategy.Password.PasswordConfirmationValidation,
                strategy_name: :password}

      change {AshAuthentication.Strategy.Password.HashPasswordChange, strategy_name: :password}
      change AshAuthentication.GenerateTokenChange
    end

    read :sign_in_with_password do
      get? true

      argument :email, :ci_string do
        allow_nil? false
      end

      argument :password, :string do
        allow_nil? false
        sensitive? true
      end

      prepare build(filter: [status: :active])
      prepare AshAuthentication.Strategy.Password.SignInPreparation
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
      where: [action_is(:bootstrap_user), attribute_does_not_equal(:status, :pending)]
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
      password :password do
        identity_field :email
        hash_provider AshAuthentication.Argon2Provider
        registration_enabled? false
        sign_in_action_name :sign_in_with_password
        sign_in_tokens_enabled? false
        require_confirmed_with :confirmed_at

        resettable do
          sender fn _user, _token, _opts -> :ok end
          token_lifetime {1, :hours}
          request_password_reset_action_name :request_password_reset
          password_reset_action_name :reset_password
        end
      end

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
      action([:bootstrap_admin, :create_pending_user, :bootstrap_user]),
      actor_attribute_equals(:accounts_bootstrap?, true)
    ] do
      authorize_if always()
    end

    policy action(:change_password) do
      authorize_if expr(id == ^actor(:id) and status == :active)
    end

    policy always() do
      forbid_if always()
    end
  end
end
