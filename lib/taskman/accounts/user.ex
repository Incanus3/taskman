defmodule Taskman.Accounts.User do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    extensions: [AshAuthentication, AshAdmin.Resource, AshRateLimiter],
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  alias Taskman.Accounts.{ApiKey, RateLimit, Token}

  rate_limit do
    backend RateLimit
  end

  alias Taskman.Accounts.User.Changes.{
    DeliverManagedEmail,
    ProtectLastAdmin,
    ReportInvitationDelivery,
    RequireActiveAdministrator
  }

  alias Taskman.Accounts.User.Checks.PersistedActiveAdministrator
  alias Taskman.Accounts.User.ManualUpdates.ManageEmail

  alias Taskman.Accounts.User.Preparations.RequireActiveAdministrator,
    as: RequireActiveAdminRead

  alias Taskman.Accounts.User.Senders.{SendConfirmation, SendInvitation, SendPasswordReset}
  alias Taskman.Accounts.User.Status

  postgres do
    table "users"
    repo Taskman.Repo
  end

  admin do
    show_action :admin_read
    read_actions [:admin_read]
    generic_actions []
    create_actions [:create_pending_user]

    update_actions []
    destroy_actions []
    table_columns [:email, :status, :admin?, :confirmed_at, :inserted_at, :updated_at]
    table_sortable_columns [:email, :status, :admin?, :confirmed_at, :inserted_at, :updated_at]
    table_filterable_columns [:email, :status, :admin?]
    show_sensitive_fields []
  end

  actions do
    defaults [:read]

    read :admin_read do
      public? false

      prepare build(
                select: [:id, :email, :status, :admin?, :confirmed_at, :inserted_at, :updated_at]
              )

      prepare RequireActiveAdminRead
    end

    create :create_pending_user do
      primary? true
      accept [:admin?, :email]
      change RequireActiveAdministrator
      change ReportInvitationDelivery
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

    update :complete_setup do
      public? false
      require_atomic? false
      accept [:email]

      argument :confirm, :string do
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

      change AshAuthentication.AddOn.Confirmation.ConfirmChange
      change set_attribute(:status, :active)
      change {AshAuthentication.Strategy.Password.HashPasswordChange, strategy_name: :password}
      change AshAuthentication.GenerateTokenChange
    end

    update :request_email_change do
      public? false
      require_atomic? false
      accept [:email]

      argument :current_password, :string do
        allow_nil? false
        sensitive? true
      end

      validate {AshAuthentication.Strategy.Password.PasswordValidation,
                strategy_name: :password, password_argument: :current_password}
    end

    update :resend_invitation do
      public? false
      require_atomic? false
      accept []
      change {ProtectLastAdmin, mode: :resend_invitation}
    end

    update :revoke_invitation do
      public? false
      require_atomic? false
      accept []
      change {ProtectLastAdmin, mode: :revoke_invitation}
    end

    update :enable do
      public? false
      require_atomic? false
      accept []
      change set_attribute(:status, :active)
      change {ProtectLastAdmin, mode: :enable}
    end

    update :disable do
      public? false
      require_atomic? false
      accept []
      change set_attribute(:status, :disabled)
      change {ProtectLastAdmin, mode: :disable}
    end

    update :promote do
      public? false
      require_atomic? false
      accept []
      change set_attribute(:admin?, true)
      change {ProtectLastAdmin, mode: :promote}
    end

    update :demote do
      public? false
      require_atomic? false
      accept []
      change set_attribute(:admin?, false)
      change {ProtectLastAdmin, mode: :demote}
    end

    update :manage_email do
      public? false
      require_atomic? false
      accept []

      argument :email, :ci_string do
        allow_nil? false
      end

      argument :confirmed?, :boolean do
        allow_nil? false
      end

      manual ManageEmail
      change DeliverManagedEmail
    end

    update :revoke_sessions do
      public? false
      require_atomic? false
      accept []
      change {ProtectLastAdmin, mode: :sessions}
    end

    update :revoke_api_keys do
      public? false
      require_atomic? false
      accept []
      change {ProtectLastAdmin, mode: :api_keys}
    end

    destroy :admin_delete do
      public? false
      require_atomic? false

      argument :confirmation, :string do
        allow_nil? false
        description "Type DELETE to permanently remove this account."
      end

      validate argument_equals(:confirmation, "DELETE")
      change {ProtectLastAdmin, mode: :admin_delete}
    end

    destroy :self_delete do
      public? false
      require_atomic? false

      argument :current_password, :string do
        allow_nil? false
        sensitive? true
      end

      validate {AshAuthentication.Strategy.Password.PasswordValidation,
                strategy_name: :password, password_argument: :current_password}

      change {ProtectLastAdmin, mode: :self_delete}
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

      prepare {AshRateLimiter.Preparation,
               limit: 10, per: :timer.minutes(15), key: &RateLimit.sign_in_email_key/2}

      prepare {AshRateLimiter.Preparation,
               limit: 60, per: :timer.minutes(15), key: &RateLimit.sign_in_ip_key/2}

      prepare AshAuthentication.Strategy.Password.SignInPreparation
    end

    read :request_password_reset do
      argument :email, :ci_string do
        allow_nil? false
      end

      prepare {AshRateLimiter.Preparation,
               limit: 5, per: :timer.hours(1), key: &RateLimit.password_reset_email_key/2}

      prepare {AshRateLimiter.Preparation,
               limit: 20, per: :timer.hours(1), key: &RateLimit.password_reset_ip_key/2}

      prepare AshAuthentication.Strategy.Password.RequestPasswordResetPreparation
    end

    read :sign_in_with_api_key do
      argument :api_key, :string do
        allow_nil? false
        sensitive? true
      end

      prepare build(filter: [status: :active])
      prepare AshAuthentication.Strategy.ApiKey.SignInPreparation
    end

    read :api_key_actor do
      get? true
      public? false
      filter expr(status == :active and not is_nil(confirmed_at) and id == ^actor(:id))
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

    has_many :valid_api_keys, ApiKey do
      filter expr(is_nil(revoked_at) and expires_at > now())
    end
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
        {:ok, Application.fetch_env!(:taskman, :token_signing_secret)}
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
          sender SendPasswordReset
          token_lifetime {1, :hours}
          request_password_reset_action_name :request_password_reset
          password_reset_action_name :reset_password
        end
      end

      api_key do
        api_key_relationship :valid_api_keys
      end
    end

    add_ons do
      confirmation :setup do
        monitor_fields [:email]
        token_lifetime {7, :days}
        confirm_on_create? true
        confirm_on_update? false
        confirm_action_name :complete_setup
        require_interaction? true
        auto_confirm_actions [:bootstrap_admin, :bootstrap_user]
        sender SendInvitation
      end

      confirmation :email_change do
        monitor_fields [:email]
        token_lifetime {24, :hours}
        confirm_on_create? false
        confirm_on_update? true
        inhibit_updates? true
        confirmed_at_field :email_change_confirmed_at
        confirm_action_name :confirm_email_change
        require_interaction? true

        auto_confirm_actions []

        sender SendConfirmation
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

    bypass action(:change_password) do
      authorize_if expr(id == ^actor(:id) and status == :active)
    end

    bypass action(:request_email_change) do
      authorize_if expr(id == ^actor(:id) and status == :active)
    end

    bypass action(:api_key_actor) do
      authorize_if expr(
                     id == ^actor(:id) and status == :active and
                       not is_nil(confirmed_at) and
                       not is_nil(^actor(:confirmed_at))
                   )
    end

    bypass action([:read, :admin_read]) do
      authorize_if PersistedActiveAdministrator
    end

    bypass [
      action(:create_pending_user),
      actor_attribute_equals(:admin?, true),
      actor_attribute_equals(:status, :active)
    ] do
      authorize_if always()
    end

    bypass [
      action([:resend_invitation, :revoke_invitation]),
      actor_attribute_equals(:admin?, true),
      actor_attribute_equals(:status, :active)
    ] do
      authorize_if expr(status == :pending)
    end

    bypass [
      action([
        :enable,
        :disable,
        :promote,
        :demote,
        :revoke_sessions,
        :revoke_api_keys
      ]),
      actor_attribute_equals(:admin?, true),
      actor_attribute_equals(:status, :active)
    ] do
      authorize_if always()
    end

    bypass [
      action([
        :manage_email,
        :admin_delete
      ]),
      actor_attribute_equals(:admin?, true),
      actor_attribute_equals(:status, :active)
    ] do
      authorize_if expr(id != ^actor(:id))
    end

    bypass action(:self_delete) do
      authorize_if expr(id == ^actor(:id) and status == :active)
    end

    policy always() do
      forbid_if always()
    end
  end
end
