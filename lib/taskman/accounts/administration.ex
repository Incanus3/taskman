defmodule Taskman.Accounts.Administration do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.{SecurityLog, User}
  alias Taskman.Repo

  require Ash.Query

  @bootstrap_actor %{accounts_bootstrap?: true}

  @spec bootstrap_admin(String.t(), String.t()) ::
          {:ok, User.t()} | {:error, term()}
  def bootstrap_admin(email, password) when is_binary(email) and is_binary(password) do
    Taskman.Accounts.create_bootstrap_admin(
      %{email: email, password: password, password_confirmation: password},
      actor: @bootstrap_actor
    )
  end

  def bootstrap_admin(_email, _password), do: {:error, :invalid_input}

  @doc false
  @spec active_administrator(User.t() | term()) :: {:ok, User.t()} | {:error, :forbidden}
  def active_administrator(%User{id: id}) do
    case Repo.one(
           from user in User,
             where: user.id == ^id and user.status == :active and user.admin? == true
         ) do
      %User{} = actor -> {:ok, actor}
      nil -> {:error, :forbidden}
    end
  end

  def active_administrator(_actor), do: {:error, :forbidden}

  @spec get_user(User.t(), Ecto.UUID.t()) :: {:ok, User.t()} | {:error, term()}
  def get_user(actor, id) when is_struct(actor, User) and is_binary(id) do
    result =
      User
      |> Ash.Query.for_read(:admin_read, %{}, actor: actor, domain: Taskman.Accounts)
      |> Ash.Query.filter(id == ^id)
      |> Ash.read_one(actor: actor, authorize?: true, domain: Taskman.Accounts)

    case result do
      {:ok, %User{} = user} -> {:ok, user}
      {:ok, nil} -> {:error, :not_found}
      {:error, _reason} = error -> error
    end
  end

  def get_user(_actor, _id), do: {:error, :invalid_input}

  @spec enable_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def enable_user(actor, user) do
    SecurityLog.audit(
      update_administrative_user(actor, user, :enable),
      :account_enabled,
      :account_enable_rejected,
      actor,
      user
    )
  end

  @spec disable_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def disable_user(actor, user) do
    SecurityLog.audit(
      update_administrative_user(actor, user, :disable),
      :account_disabled,
      :account_disable_rejected,
      actor,
      user
    )
  end

  @spec promote_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def promote_user(actor, user) do
    SecurityLog.audit(
      update_administrative_user(actor, user, :promote),
      :administrator_granted,
      :administrator_grant_rejected,
      actor,
      user
    )
  end

  @spec demote_user(User.t(), User.t()) :: {:ok, User.t()} | {:error, term()}
  def demote_user(actor, user) do
    SecurityLog.audit(
      update_administrative_user(actor, user, :demote),
      :administrator_revoked,
      :administrator_revocation_rejected,
      actor,
      user
    )
  end

  @spec revoke_user_sessions(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_user_sessions(actor, user) do
    result =
      case update_administrative_user(actor, user, :revoke_sessions) do
        {:ok, _user} -> :ok
        {:error, _reason} = error -> error
      end

    SecurityLog.audit(result, :sessions_revoked, :sessions_revocation_rejected, actor, user)
  end

  @spec revoke_user_api_keys(User.t(), User.t()) :: :ok | {:error, term()}
  def revoke_user_api_keys(actor, user) do
    result =
      case update_administrative_user(actor, user, :revoke_api_keys) do
        {:ok, _user} -> :ok
        {:error, _reason} = error -> error
      end

    SecurityLog.audit(result, :api_keys_revoked, :api_keys_revocation_rejected, actor, user)
  end

  @spec manage_email(User.t(), User.t(), String.t(), boolean()) ::
          {:ok, User.t()} | {:error, term()}
  def manage_email(actor, user, email, confirmed?)
      when is_struct(actor, User) and is_struct(user, User) and is_binary(email) and
             is_boolean(confirmed?) do
    result =
      user
      |> Ash.Changeset.for_update(:manage_email, %{email: email, confirmed?: confirmed?})
      |> Ash.update(actor: actor, authorize?: true, domain: Taskman.Accounts)

    result
    |> normalize_managed_email_result()
    |> SecurityLog.audit(:email_managed, :email_management_rejected, actor, user)
  end

  def manage_email(_actor, _user, _email, _confirmed?), do: {:error, :invalid_input}

  @doc false
  @spec lock_actor_target_and_active_administrators(User.t() | term(), Ecto.UUID.t()) ::
          {:ok, User.t(), User.t(), [User.t()]} | :error
  def lock_actor_target_and_active_administrators(%User{id: actor_id}, target_id) do
    principals =
      Repo.all(
        from user in User,
          where:
            user.id == ^actor_id or user.id == ^target_id or
              (user.status == :active and user.admin? == true),
          order_by: [asc: user.id],
          lock: "FOR UPDATE"
      )

    with %User{} = actor <- Enum.find(principals, &(&1.id == actor_id)),
         %User{} = target <- Enum.find(principals, &(&1.id == target_id)) do
      active_administrators =
        Enum.filter(principals, &(&1.status == :active and &1.admin?))

      {:ok, actor, target, active_administrators}
    else
      _ -> :error
    end
  end

  def lock_actor_target_and_active_administrators(_actor, _target_id), do: :error

  @doc false
  @spec lock_active_administrator(User.t() | term()) :: {:ok, User.t()} | :error
  def lock_active_administrator(%User{id: actor_id}) do
    case Repo.one(from user in User, where: user.id == ^actor_id, lock: "FOR UPDATE") do
      %User{status: :active, admin?: true} = actor -> {:ok, actor}
      _actor -> :error
    end
  end

  def lock_active_administrator(_actor), do: :error

  @doc false
  @spec active_administrator?(User.t()) :: boolean()
  def active_administrator?(%User{status: :active, admin?: true}), do: true
  def active_administrator?(_user), do: false

  @doc false
  @spec persisted_active_administrator?(User.t() | term()) :: boolean()
  def persisted_active_administrator?(%User{id: actor_id}) do
    Repo.exists?(
      from user in User,
        where: user.id == ^actor_id and user.status == :active and user.admin? == true
    )
  end

  def persisted_active_administrator?(_actor), do: false

  defp update_administrative_user(actor, user, action)
       when is_struct(actor, User) and is_struct(user, User) do
    user
    |> Ash.Changeset.for_update(action, %{})
    |> Ash.update(actor: actor, authorize?: true, domain: Taskman.Accounts)
  end

  defp update_administrative_user(_actor, _user, _action), do: {:error, :invalid_input}

  defp normalize_managed_email_result(
         {:error, %{errors: [%{value: [delivery_failed: %User{} = user]} | _]}}
       ) do
    {:error, {:delivery_failed, user}}
  end

  defp normalize_managed_email_result(result), do: result
end
