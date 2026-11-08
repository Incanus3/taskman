defmodule Taskman.Accounts do
  @moduledoc "Account authentication, credential management, and administrative use cases."

  use Ash.Domain, extensions: [AshAdmin.Domain]

  alias Taskman.Accounts.{
    AccountClosure,
    Administration,
    ApiKey,
    ApiKeys,
    Authentication,
    Delivery,
    EmailChanges,
    Invitations,
    Passwords,
    Sessions,
    User
  }

  authorization do
    authorize :always
  end

  admin do
    show? true
    show_resources [Taskman.Accounts.User]
  end

  resources do
    resource Taskman.Accounts.User do
      define :create_pending_user,
        action: :create_pending_user,
        default_options: [authorize?: true]

      define :bootstrap_user, action: :bootstrap_user, default_options: [authorize?: true]

      define :create_bootstrap_admin,
        action: :bootstrap_admin,
        default_options: [authorize?: true]
    end

    resource Taskman.Accounts.ApiKey do
      define :create_api_key_record,
        action: :create_for_bootstrap,
        default_options: [authorize?: true]
    end

    resource Taskman.Accounts.Token
  end

  @spec bootstrap_admin(String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def bootstrap_admin(email, password),
    do: Administration.bootstrap_admin(email, password)

  @spec sign_in_with_password(map()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_password(params),
    do: Authentication.sign_in_with_password(params)

  @spec sign_in_with_api_key(map()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params) when is_map(params),
    do: Authentication.sign_in_with_api_key(params)

  @spec sign_in_with_api_key(map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params, opts),
    do: Authentication.sign_in_with_api_key(params, opts)

  @spec create_api_key(User.t(), map()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs) when is_map(attrs),
    do: ApiKeys.create_api_key(actor, attrs)

  @spec create_api_key(User.t(), map(), keyword()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs, opts),
    do: ApiKeys.create_api_key(actor, attrs, opts)

  @spec list_api_keys(User.t()) :: {:ok, [ApiKey.t()]} | {:error, term()}
  def list_api_keys(actor),
    do: ApiKeys.list_api_keys(actor)

  @spec revoke_api_key(User.t(), Ecto.UUID.t()) :: :ok | {:error, term()}
  def revoke_api_key(actor, id),
    do: ApiKeys.revoke_api_key(actor, id)

  @spec account_settings_state(User.t()) ::
          {:ok, %{user: User.t(), pending_email: String.t() | nil}} | {:error, term()}
  def account_settings_state(actor),
    do: Sessions.account_settings_state(actor)

  @spec list_sessions(User.t(), keyword()) :: {:ok, [map()]} | {:error, term()}
  def list_sessions(actor, opts \\ []),
    do: Sessions.list_sessions(actor, opts)

  @spec revoke_session(User.t(), String.t()) :: :ok | {:error, term()}
  def revoke_session(actor, jti),
    do: Sessions.revoke_session(actor, jti)

  @spec revoke_other_sessions(User.t(), String.t()) :: :ok | {:error, term()}
  def revoke_other_sessions(actor, acting_token),
    do: Sessions.revoke_other_sessions(actor, acting_token)

  @spec change_password(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def change_password(actor, params),
    do: Passwords.change_password(actor, params)

  @spec change_password(User.t(), String.t(), map()) ::
          {:ok, %{user: User.t(), replacement_session: String.t()}} | {:error, term()}
  def change_password(actor, acting_token, params),
    do: Passwords.change_password(actor, acting_token, params)

  @spec invite_user(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def invite_user(actor, params),
    do: Invitations.invite_user(actor, params)

  @spec resend_invitation(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def resend_invitation(actor, user),
    do: Invitations.resend_invitation(actor, user)

  @spec revoke_invitation(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_invitation(actor, user),
    do: Invitations.revoke_invitation(actor, user)

  @spec complete_setup(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def complete_setup(token, params, opts \\ []),
    do: Invitations.complete_setup(token, params, opts)

  @spec request_email_change(User.t(), String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password),
    do: EmailChanges.request_email_change(actor, email, current_password)

  @spec request_email_change(User.t(), String.t(), String.t(), keyword()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password, opts),
    do: EmailChanges.request_email_change(actor, email, current_password, opts)

  @spec confirm_email_change(String.t(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def confirm_email_change(token, opts \\ []),
    do: EmailChanges.confirm_email_change(token, opts)

  @spec request_password_reset(String.t(), keyword()) ::
          :ok | {:error, retry_after: pos_integer()}
  def request_password_reset(email, opts \\ []),
    do: Passwords.request_password_reset(email, opts)

  @spec reset_password(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def reset_password(token, params, opts \\ []),
    do: Passwords.reset_password(token, params, opts)

  @spec get_admin_user(User.t(), Ecto.UUID.t()) :: {:ok, User.t()} | {:error, term()}
  def get_admin_user(actor, id),
    do: Administration.get_user(actor, id)

  @spec enable_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def enable_user(actor, user),
    do: Administration.enable_user(actor, user)

  @spec disable_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def disable_user(actor, user),
    do: Administration.disable_user(actor, user)

  @spec promote_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def promote_user(actor, user),
    do: Administration.promote_user(actor, user)

  @spec demote_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def demote_user(actor, user),
    do: Administration.demote_user(actor, user)

  @spec revoke_user_sessions(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_user_sessions(actor, user),
    do: Administration.revoke_user_sessions(actor, user)

  @spec revoke_user_api_keys(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_user_api_keys(actor, user),
    do: Administration.revoke_user_api_keys(actor, user)

  @doc false
  @spec persisted_active_administrator?(User.t() | term()) :: boolean()
  def persisted_active_administrator?(actor),
    do: Administration.persisted_active_administrator?(actor)

  @spec manage_email(User.t(), User.t(), String.t(), boolean()) ::
          {:ok, User.t()} | {:error, term()}
  def manage_email(actor, user, email, confirmed?),
    do: Administration.manage_email(actor, user, email, confirmed?)

  @spec delete_user(User.t(), User.t(), String.t()) :: :ok | {:error, term()}
  def delete_user(actor, user, confirmation),
    do: AccountClosure.delete_user(actor, user, confirmation)

  @spec delete_user(User.t(), User.t()) :: :ok | {:error, term()}
  def delete_user(actor, user),
    do: AccountClosure.delete_user(actor, user)

  @spec delete_own_account(User.t(), String.t()) :: :ok | {:error, term()}
  def delete_own_account(actor, current_password),
    do: AccountClosure.delete_own_account(actor, current_password)

  @spec emails_by_user_id([Ecto.UUID.t()]) :: %{optional(Ecto.UUID.t()) => String.t()}
  def emails_by_user_id(user_ids),
    do: User.Persistence.emails_by_user_id(user_ids)

  @doc false
  @spec lock_eligible_user(User.t() | term()) ::
          {:ok, User.t()} | {:error, :authentication_required}
  def lock_eligible_user(%User{id: id}) do
    with {:ok, persisted} <- User.Persistence.lock(id),
         true <- Authentication.eligible?(persisted) do
      {:ok, persisted}
    else
      _ -> {:error, :authentication_required}
    end
  end

  def lock_eligible_user(_actor), do: {:error, :authentication_required}

  @doc false
  @spec record_delivery_result(atom(), String.t(), :ok | {:error, term()}) :: :ok
  def record_delivery_result(purpose, token, result),
    do: Delivery.record_result(purpose, token, result)

  @doc false
  @spec log_delivery_failure(:ok | {:error, term()}) :: :ok
  def log_delivery_failure(result),
    do: Delivery.log_failure(result)
end
