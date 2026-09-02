defmodule Taskman.Accounts do
  use Ash.Domain

  import Ash.Expr

  require Logger
  require Ash.Query

  alias AshAuthentication.{AddOn.Confirmation, Info, Jwt, Strategy}
  alias Taskman.Accounts.{Emails, Token, User}

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

  @spec invite_user(User.t(), map()) :: {:ok, User.t()} | {:error, term()}
  def invite_user(actor, params) when is_map(params) do
    with_delivery_result(:setup, fn -> create_pending_user(params, actor: actor) end, fn
      {:ok, user}, :ok, _token -> {:ok, user}
      {:ok, user}, {:error, :delivery_failed}, _token -> {:error, {:delivery_failed, user}}
      {:ok, user}, nil, _token -> {:error, {:delivery_failed, user}}
      error, _result, _token -> error
    end)
  end

  def invite_user(_actor, _params), do: {:error, :invalid_input}

  @spec resend_invitation(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def resend_invitation(actor, user) do
    with {:ok, user} <- update_user(user, :resend_invitation, %{}, actor: actor),
         :ok <- Token.revoke_for_subject(user, "setup"),
         {:ok, token} <- setup_token(user) do
      case Emails.deliver_invitation(to_string(user.email), token) do
        :ok -> {:ok, user}
        {:error, :delivery_failed} -> {:error, {:delivery_failed, user}}
      end
    end
  end

  @spec revoke_invitation(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_invitation(actor, user) do
    with {:ok, user} <- update_user(user, :revoke_invitation, %{}, actor: actor),
         :ok <- Token.revoke_for_subject(user, "setup") do
      :ok
    end
  end

  @spec complete_setup(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def complete_setup(token, params, opts \\ [])

  def complete_setup(token, params, opts) when is_binary(token) and is_map(params) do
    with :ok <- Token.valid_for_purpose?(token, "setup", now(opts)),
         {:ok, user} <-
           Strategy.action(
             Info.strategy!(User, :setup),
             :confirm,
             Map.put(params, "confirm", token),
             domain: __MODULE__
           ) do
      {:ok, user}
    end
  end

  def complete_setup(_token, _params, _opts), do: {:error, :invalid_input}

  @spec request_email_change(User.t(), String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def request_email_change(actor, email, current_password)
      when is_binary(email) and is_binary(current_password) do
    with_delivery_result(
      :email_change,
      fn ->
        update_user(
          actor,
          :request_email_change,
          %{email: email, current_password: current_password},
          actor: actor
        )
      end,
      fn
        {:ok, user}, :ok, token when is_binary(token) ->
          with :ok <- revoke_older_tokens(user, "email_change", token) do
            {:ok, user}
          end

        {:ok, _user}, {:error, :delivery_failed}, _token ->
          {:error, :delivery_failed}

        {:ok, _user}, nil, _token ->
          {:error, :delivery_failed}

        error, _result, _token ->
          error
      end
    )
  end

  def request_email_change(_actor, _email, _current_password), do: {:error, :invalid_input}

  @spec confirm_email_change(String.t(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def confirm_email_change(token, opts \\ [])

  def confirm_email_change(token, opts) when is_binary(token) do
    with :ok <- Token.valid_for_purpose?(token, "email_change", now(opts)),
         {:ok, user} <-
           Strategy.action(
             Info.strategy!(User, :email_change),
             :confirm,
             %{"confirm" => token},
             domain: __MODULE__
           ) do
      {:ok, user}
    end
  end

  def confirm_email_change(_token, _opts), do: {:error, :invalid_input}

  @spec request_password_reset(String.t()) :: :ok
  def request_password_reset(email) when is_binary(email) do
    case eligible_user_by_email(email) do
      {:ok, %User{} = user} ->
        with :ok <- Token.revoke_for_subject(user, "password_reset"),
             {:ok, token, _claims} <-
               Jwt.token_for_user(user, %{"act" => "reset_password"},
                 token_lifetime: {1, :hours},
                 purpose: :password_reset
               ),
             :ok <- Emails.deliver_password_reset(to_string(user.email), token) do
          :ok
        else
          _ ->
            Logger.warning("Password reset email delivery failed")
            :ok
        end

      _ ->
        :ok
    end
  end

  def request_password_reset(_email), do: :ok

  @spec reset_password(String.t(), map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def reset_password(token, params, opts \\ [])

  def reset_password(token, params, opts) when is_binary(token) and is_map(params) do
    with :ok <- Token.valid_for_purpose?(token, "password_reset", now(opts)),
         {:ok, %{"sub" => subject}, _resource} <- Jwt.verify(token, User),
         {:ok, user} <- AshAuthentication.subject_to_user(subject, User, domain: __MODULE__),
         true <- eligible?(user),
         {:ok, updated} <-
           Strategy.action(
             Info.strategy!(User, :password),
             :reset,
             Map.put(params, "reset_token", token),
             domain: __MODULE__
           ),
         :ok <- Token.revoke_all_for_subject(updated) do
      {:ok, updated}
    else
      false -> {:error, :invalid_token}
      {:error, _error} = error -> error
      _ -> {:error, :invalid_token}
    end
  end

  def reset_password(_token, _params, _opts), do: {:error, :invalid_input}

  @doc false
  @spec record_delivery_result(atom(), String.t(), :ok | {:error, :delivery_failed}) :: :ok
  def record_delivery_result(purpose, token, result) do
    Process.put(@delivery_result_key, {purpose, result, token})
    :ok
  end

  @doc false
  @spec log_delivery_failure(:ok | {:error, :delivery_failed}) :: :ok
  def log_delivery_failure({:error, :delivery_failed}) do
    Logger.warning("Transactional email delivery failed")
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

  defp eligible_user_by_email(email) do
    User
    |> Ash.Query.new()
    |> Ash.Query.filter(email: email, status: :active)
    |> Ash.Query.filter(expr(not is_nil(confirmed_at)))
    |> Ash.Query.set_context(%{private: %{ash_authentication?: true}})
    |> Ash.read_one(domain: __MODULE__)
  end

  defp eligible?(user), do: user.status == :active and not is_nil(user.confirmed_at)
  defp now(opts), do: Keyword.get(opts, :now, DateTime.utc_now())
end
