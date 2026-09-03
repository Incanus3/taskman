defmodule Taskman.Accounts do
  use Ash.Domain

  import Ecto.Query, only: [from: 2]

  require Ash.Query

  require Logger

  alias AshAuthentication.{AddOn.Confirmation, Errors.AuthenticationFailed, Info, Jwt, Strategy}
  alias Taskman.Accounts.{ApiKey, Emails, Token, User}
  alias Taskman.Repo

  @api_key_lifetime_days 365
  @api_key_minimum_lifetime_seconds 86_400
  @api_key_lifetime_seconds @api_key_lifetime_days * 86_400

  authorization do
    authorize :always
  end

  resources do
    resource Taskman.Accounts.User do
      define :create_pending_user, action: :create_pending_user
      define :bootstrap_user, action: :bootstrap_user
      define :create_bootstrap_admin, action: :bootstrap_admin
    end

    resource Taskman.Accounts.ApiKey do
      define :create_api_key_record, action: :create_for_bootstrap
    end

    resource Taskman.Accounts.Token
  end

  @bootstrap_actor %{accounts_bootstrap?: true}
  @delivery_result_key {__MODULE__, :delivery_result}

  @spec bootstrap_admin(map()) :: {:ok, Taskman.Accounts.User.t()} | {:error, term()}
  def bootstrap_admin(params) when is_map(params) do
    params
    |> Map.take([:email, :password, :password_confirmation])
    |> create_bootstrap_admin(actor: @bootstrap_actor)
  end

  def bootstrap_admin(_params), do: {:error, :invalid_input}

  @spec sign_in_with_password(map()) :: {:ok, Taskman.Accounts.User.t()} | {:error, term()}
  def sign_in_with_password(params) when is_map(params) do
    Taskman.Accounts.User
    |> AshAuthentication.Info.strategy!(:password)
    |> AshAuthentication.Strategy.action(:sign_in, params, domain: __MODULE__)
  end

  def sign_in_with_password(_params), do: {:error, :invalid_credentials}

  @spec sign_in_with_api_key(map()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params) when is_map(params), do: sign_in_with_api_key(params, [])

  @spec sign_in_with_api_key(map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params, opts) when is_map(params) and is_list(opts) do
    with {:ok, api_key} <- api_key_param(params),
         true <- valid_api_key_format?(api_key),
         {:ok, user} <-
           User
           |> Info.strategy!(:api_key)
           |> Strategy.action(:sign_in, %{api_key: api_key}, api_key_strategy_opts(opts)),
         true <- eligible?(user) do
      {:ok, user}
    else
      {:error, _reason} -> {:error, authentication_failed()}
      false -> {:error, authentication_failed()}
    end
  end

  def sign_in_with_api_key(_params, _opts), do: {:error, authentication_failed()}

  @spec create_api_key(User.t(), map()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs) when is_map(attrs), do: create_api_key(actor, attrs, [])

  @spec create_api_key(User.t(), map(), keyword()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs, opts) when is_map(attrs) and is_list(opts) do
    with :ok <- require_eligible_actor(actor),
         {:ok, name} <- api_key_name(attrs),
         {:ok, expires_at} <- api_key_expiration(attrs, opts),
         {:ok, api_key} <-
           ApiKey
           |> Ash.Changeset.for_create(:create_for_user, %{
             name: name,
             expires_at: expires_at,
             user_id: actor.id
           })
           |> Ash.create(actor: actor, domain: __MODULE__),
         plaintext when is_binary(plaintext) <-
           Ash.Resource.get_metadata(api_key, :plaintext_api_key) do
      {:ok, %{api_key: api_key, plaintext: plaintext}}
    else
      nil -> {:error, :api_key_generation_failed}
      {:error, _reason} = error -> error
      _ -> {:error, :invalid_input}
    end
  end

  def create_api_key(_actor, _attrs, _opts), do: {:error, :invalid_input}

  @spec list_api_keys(User.t()) :: {:ok, [ApiKey.t()]} | {:error, term()}
  def list_api_keys(actor) do
    with :ok <- require_eligible_actor(actor),
         {:ok, keys} <- list_owned_api_keys(actor) do
      {:ok, keys}
    end
  end

  @spec revoke_api_key(User.t(), Ecto.UUID.t()) :: :ok | {:error, term()}
  def revoke_api_key(actor, id) do
    with :ok <- require_eligible_actor(actor),
         {:ok, uuid} <- Ecto.UUID.cast(id),
         {:ok, api_key} <- fetch_owned_api_key(actor, uuid),
         {:ok, _revoked} <-
           api_key
           |> Ash.Changeset.for_update(:revoke, %{})
           |> Ash.update(actor: actor, domain: __MODULE__) do
      :ok
    else
      :error -> {:error, :not_found}
      {:error, _reason} = error -> error
    end
  end

  defp require_eligible_actor(%User{} = actor) do
    if eligible?(actor), do: :ok, else: {:error, :authentication_required}
  end

  defp require_eligible_actor(_actor), do: {:error, :authentication_required}

  defp api_key_param(params) do
    case Map.get(params, :api_key, Map.get(params, "api_key")) do
      api_key when is_binary(api_key) -> {:ok, api_key}
      _ -> {:error, :invalid_credentials}
    end
  end

  defp valid_api_key_format?(api_key) do
    Regex.match?(~r/\Atm_[A-Za-z0-9]+_[A-Za-z0-9]+\z/, api_key)
  end

  defp api_key_strategy_opts(opts) do
    opts
    |> Keyword.take([:tenant, :context])
    |> Keyword.put(:domain, __MODULE__)
  end

  defp api_key_name(attrs) do
    case Map.get(attrs, :name, Map.get(attrs, "name")) do
      name when is_binary(name) ->
        name = String.trim(name)
        if name == "", do: {:error, :invalid_name}, else: {:ok, name}

      _ ->
        {:error, :invalid_name}
    end
  end

  defp api_key_expiration(attrs, opts) do
    now = Keyword.get(opts, :now, DateTime.utc_now())

    case Map.fetch(attrs, :expires_at) do
      :error ->
        case Map.fetch(attrs, "expires_at") do
          :error -> {:error, :invalid_expiration}
          {:ok, value} -> validate_api_key_expiration(value, now)
        end

      {:ok, value} ->
        validate_api_key_expiration(value, now)
    end
  end

  defp validate_api_key_expiration(%DateTime{} = expires_at, %DateTime{} = now) do
    comparison_now = DateTime.truncate(now, :second)
    comparison_expiry = DateTime.truncate(expires_at, :second)
    minimum_expiry = DateTime.add(comparison_now, @api_key_minimum_lifetime_seconds, :second)
    max_expiry = DateTime.add(comparison_now, @api_key_lifetime_seconds, :second)

    if DateTime.compare(comparison_expiry, minimum_expiry) != :lt and
         DateTime.compare(comparison_expiry, max_expiry) != :gt do
      {:ok, expires_at}
    else
      {:error, :invalid_expiration}
    end
  end

  defp validate_api_key_expiration(_expires_at, _now), do: {:error, :invalid_expiration}

  defp fetch_owned_api_key(actor, id) do
    query =
      ApiKey
      |> Ash.Query.for_read(:get_for_user, %{}, actor: actor, domain: __MODULE__)
      |> Ash.Query.filter(id: id)

    case Ash.read_one(query, actor: actor, domain: __MODULE__) do
      {:ok, %ApiKey{} = api_key} -> {:ok, api_key}
      {:ok, nil} -> {:error, :not_found}
      {:error, _reason} = error -> error
    end
  end

  defp list_owned_api_keys(actor) do
    query =
      ApiKey
      |> Ash.Query.for_read(:list_for_user, %{}, actor: actor, domain: __MODULE__)
      |> Ash.Query.sort(inserted_at: :asc, id: :asc)

    Ash.read(query, actor: actor, domain: __MODULE__)
  end

  defp authentication_failed do
    AuthenticationFailed.exception(caused_by: %{module: __MODULE__, action: :sign_in})
  end

  @spec invite_user(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def invite_user(actor, params) when is_map(params) do
    with_delivery_result(:setup, fn -> create_pending_user(params, actor: actor) end, fn
      {:ok, user}, :ok, _token ->
        {:ok, user}

      {:ok, user}, {:error, _reason} = delivery_error, _token ->
        log_delivery_failure(delivery_error)
        {:error, {:delivery_failed, user}}

      {:ok, user}, nil, _token ->
        {:error, {:delivery_failed, user}}

      error, _result, _token ->
        error
    end)
  end

  def invite_user(_actor, _params), do: {:error, :invalid_input}

  @spec resend_invitation(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def resend_invitation(actor, user) do
    case transaction(fn ->
           with {:ok, locked_user} <- lock_user(user.id),
                {:ok, updated_user} <-
                  update_user(locked_user, :resend_invitation, %{}, actor: actor),
                :ok <- Token.revoke_for_subject(updated_user, "setup"),
                {:ok, token} <- setup_token(updated_user) do
             {:ok, {updated_user, token}}
           end
         end) do
      {:ok, {updated_user, token}} ->
        case Emails.deliver_invitation(to_string(updated_user.email), token) do
          :ok ->
            {:ok, updated_user}

          {:error, _reason} = delivery_error ->
            log_delivery_failure(delivery_error)
            {:error, {:delivery_failed, updated_user}}
        end

      {:error, _reason} = error ->
        error
    end
  end

  @spec revoke_invitation(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_invitation(actor, user) do
    case transaction(fn ->
           with {:ok, locked_user} <- lock_user(user.id),
                {:ok, updated_user} <-
                  update_user(locked_user, :revoke_invitation, %{}, actor: actor),
                :ok <- Token.revoke_for_subject(updated_user, "setup") do
             {:ok, :ok}
           end
         end) do
      {:ok, :ok} -> :ok
      {:error, _reason} = error -> error
    end
  end

  @spec complete_setup(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def complete_setup(token, params, opts \\ [])

  def complete_setup(token, params, opts) when is_binary(token) and is_map(params) do
    with {:ok, user} <- token_user(token) do
      transaction(fn ->
        with {:ok, _locked_user} <- lock_user(user.id),
             {:ok, _stored_token} <- Token.claim_for_redemption(token, "setup", now(opts)),
             {:ok, completed_user} <-
               Strategy.action(
                 Info.strategy!(User, :setup),
                 :confirm,
                 Map.put(params, "confirm", token),
                 domain: __MODULE__
               ) do
          {:ok, completed_user}
        end
      end)
    end
  end

  def complete_setup(_token, _params, _opts), do: {:error, :invalid_input}

  @spec request_email_change(User.t(), String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password)
      when is_binary(email) and is_binary(current_password) do
    case transaction(fn ->
           with {:ok, locked_user} <- lock_user(actor.id) do
             with_delivery_result(
               :email_change,
               fn ->
                 update_user(
                   locked_user,
                   :request_email_change,
                   %{email: email, current_password: current_password},
                   actor: actor
                 )
               end,
               fn
                 {:ok, user}, delivery_result, token when is_binary(token) ->
                   with :ok <- revoke_older_tokens(user, "email_change", token) do
                     {:ok, {user, delivery_result}}
                   end

                 {:ok, _user}, nil, _token ->
                   {:error, :delivery_failed}

                 error, _delivery_result, _token ->
                   error
               end
             )
           end
         end) do
      {:ok, {user, :ok}} ->
        {:ok, user}

      {:ok, {_user, {:error, _reason} = delivery_error}} ->
        log_delivery_failure(delivery_error)
        {:error, :delivery_failed}

      {:error, _reason} = error ->
        error
    end
  end

  def request_email_change(_actor, _email, _current_password), do: {:error, :invalid_input}

  @spec confirm_email_change(String.t(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def confirm_email_change(token, opts \\ [])

  def confirm_email_change(token, opts) when is_binary(token) do
    with {:ok, user} <- token_user(token) do
      transaction(fn ->
        with {:ok, _locked_user} <- lock_user(user.id),
             {:ok, _stored_token} <- Token.claim_for_redemption(token, "email_change", now(opts)),
             {:ok, changed_user} <-
               Strategy.action(
                 Info.strategy!(User, :email_change),
                 :confirm,
                 %{"confirm" => token},
                 domain: __MODULE__
               ) do
          {:ok, changed_user}
        end
      end)
    end
  end

  def confirm_email_change(_token, _opts), do: {:error, :invalid_input}

  @spec request_password_reset(String.t()) :: :ok
  def request_password_reset(email) when is_binary(email) do
    case transaction(fn ->
           with {:ok, user} <- lock_eligible_user_by_email(email),
                :ok <- Token.revoke_for_subject(user, "password_reset"),
                {:ok, token, _claims} <-
                  Jwt.token_for_user(user, %{"act" => "reset_password"},
                    token_lifetime: {1, :hours},
                    purpose: :password_reset
                  ) do
             {:ok, {user, token}}
           end
         end) do
      {:ok, {user, token}} ->
        case Emails.deliver_password_reset(to_string(user.email), token) do
          :ok ->
            :ok

          {:error, _reason} = delivery_error ->
            log_delivery_failure(delivery_error)
            :ok
        end

      {:error, _reason} ->
        :ok
    end
  end

  def request_password_reset(_email), do: :ok

  @spec reset_password(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def reset_password(token, params, opts \\ [])

  def reset_password(token, params, opts) when is_binary(token) and is_map(params) do
    with {:ok, user} <- token_user(token) do
      transaction(fn ->
        with {:ok, locked_user} <- lock_user(user.id),
             true <- eligible?(locked_user),
             {:ok, _stored_token} <-
               Token.claim_for_redemption(token, "password_reset", now(opts)),
             {:ok, updated_user} <-
               Strategy.action(
                 Info.strategy!(User, :password),
                 :reset,
                 Map.put(params, "reset_token", token),
                 domain: __MODULE__
               ),
             {:ok, revoked_jtis} <-
               Token.revoke_browser_sessions_with_jtis(updated_user, opts) do
          {:ok, {updated_user, revoked_jtis}}
        else
          false -> {:error, :invalid_token}
          {:error, _reason} = error -> error
          _ -> {:error, :invalid_token}
        end
      end)
      |> case do
        {:ok, {updated_user, revoked_jtis}} ->
          case Token.broadcast_session_jtis(revoked_jtis) do
            :ok -> {:ok, updated_user}
            {:error, _reason} = error -> error
          end

        {:error, _reason} = error ->
          error
      end
    end
  end

  def reset_password(_token, _params, _opts), do: {:error, :invalid_input}

  @doc false
  @spec record_delivery_result(atom(), String.t(), :ok | {:error, term()}) :: :ok
  def record_delivery_result(purpose, token, result) do
    Process.put(@delivery_result_key, {purpose, result, token})
    :ok
  end

  @doc false
  @spec log_delivery_failure(:ok | {:error, term()}) :: :ok
  def log_delivery_failure({:error, {:delivery_failed, delivery_class}})
      when is_atom(delivery_class) do
    Logger.warning("Transactional email delivery failed (class=#{delivery_class})")
    :ok
  end

  def log_delivery_failure({:error, _reason}) do
    Logger.warning("Transactional email delivery failed (class=unknown)")
    :ok
  end

  def log_delivery_failure(:ok), do: :ok

  defp with_delivery_result(purpose, operation, handle) do
    Process.delete(@delivery_result_key)
    result = operation.()

    case Process.delete(@delivery_result_key) do
      {^purpose, delivery_result, token} -> handle.(result, delivery_result, token)
      nil -> handle.(result, nil, nil)
    end
  end

  defp transaction(operation) do
    case Ash.transact([User, Token], operation) do
      {:ok, {:ok, value}} -> {:ok, value}
      {:error, reason} -> {:error, reason}
    end
  end

  defp update_user(user, action, params, opts) do
    user
    |> Ash.Changeset.for_update(action, params)
    |> Ash.update(Keyword.put(opts, :domain, __MODULE__))
  end

  defp setup_token(user) do
    Confirmation.confirmation_token(
      Info.strategy!(User, :setup),
      Ash.Changeset.new(user),
      user,
      domain: __MODULE__
    )
    |> case do
      {:ok, token} -> {:ok, token}
      _ -> {:error, :token_generation_failed}
    end
  end

  defp revoke_older_tokens(user, purpose, current_token) do
    with {:ok, %{"jti" => current_jti}} <- Jwt.peek(current_token),
         :ok <- Token.revoke_for_subject_except(user, purpose, current_jti) do
      :ok
    else
      _ -> {:error, :token_revocation_failed}
    end
  end

  defp lock_user(id) do
    case Repo.one(from user in User, where: user.id == ^id, lock: "FOR UPDATE") do
      %User{} = user -> {:ok, user}
      nil -> {:error, :invalid_token}
    end
  end

  defp lock_eligible_user_by_email(email) do
    case Repo.one(
           from user in User,
             where:
               user.email == ^email and user.status == :active and not is_nil(user.confirmed_at),
             lock: "FOR UPDATE"
         ) do
      %User{} = user -> {:ok, user}
      nil -> {:error, :ineligible_account}
    end
  end

  defp token_user(token) do
    with {:ok, %{"sub" => subject}, _resource} <- Jwt.verify(token, User),
         {:ok, user} <- AshAuthentication.subject_to_user(subject, User, domain: __MODULE__) do
      {:ok, user}
    else
      _ -> {:error, :invalid_token}
    end
  end

  defp eligible?(user), do: user.status == :active and not is_nil(user.confirmed_at)
  defp now(opts), do: Keyword.get(opts, :now, DateTime.utc_now())
end
