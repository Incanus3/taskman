defmodule Taskman.Accounts.User.ManualUpdates.ManageEmail do
  @moduledoc false

  use Ash.Resource.ManualUpdate

  import Ecto.Query, only: [from: 2]

  alias AshAuthentication.{AddOn.Confirmation, Info}
  alias Taskman.Accounts.{Administration, Token, User}
  alias Taskman.Repo

  @impl true
  def update(changeset, _opts, context) do
    with {:ok, actor, target, _active_administrators} <-
           Administration.lock_actor_target_and_active_administrators(
             context.actor,
             changeset.data.id
           ),
         true <- Administration.active_administrator?(actor),
         true <- actor.id != target.id,
         email <- Ash.Changeset.get_argument(changeset, :email),
         email when is_binary(email) <- to_string(email),
         confirmed? when is_boolean(confirmed?) <-
           Ash.Changeset.get_argument(changeset, :confirmed?) do
      manage_email(target, normalize_email(email), confirmed?)
    else
      _ -> {:error, :forbidden}
    end
  end

  defp manage_email(%User{status: :pending} = target, email, confirmed?) do
    case {email_changed?(target, email), confirmed?} do
      {false, false} ->
        {:error, :no_effect}

      {false, true} ->
        update_user(target, %{confirmed_at: DateTime.utc_now()})

      {true, confirmed?} ->
        with {:ok, updated_user} <-
               update_user(target, pending_email_attributes(email, confirmed?)),
             :ok <- delete_tokens(updated_user, ["setup", "email_change"]),
             {:ok, token} <- setup_token(updated_user) do
          {:ok, with_delivery(updated_user, :setup, token)}
        end
    end
  end

  defp manage_email(%User{status: status} = target, email, confirmed?)
       when status in [:active, :disabled] do
    case {email_changed?(target, email), confirmed?} do
      {false, false} ->
        {:error, :no_effect}

      {false, true} ->
        update_user(target, %{confirmed_at: DateTime.utc_now()})

      {true, true} ->
        with {:ok, updated_user} <-
               update_user(target, %{email: email, confirmed_at: DateTime.utc_now()}),
             :ok <- delete_tokens(updated_user, ["email_change"]) do
          {:ok, updated_user}
        end

      {true, false} ->
        with :ok <- delete_tokens(target, ["email_change"]),
             {:ok, token} <- email_change_token(target, email) do
          {:ok, with_delivery(target, :email_change, token, email)}
        end
    end
  end

  defp manage_email(_target, _email, _confirmed?), do: {:error, :invalid_lifecycle_state}

  defp update_user(user, attributes) do
    user
    |> Ecto.Changeset.cast(attributes, [:email, :confirmed_at])
    |> Ecto.Changeset.unique_constraint(:email)
    |> Repo.update()
  end

  defp pending_email_attributes(email, true),
    do: %{email: email, confirmed_at: DateTime.utc_now()}

  defp pending_email_attributes(email, false), do: %{email: email}

  defp setup_token(user) do
    Confirmation.confirmation_token(
      Info.strategy!(User, :setup),
      Ash.Changeset.new(user),
      user,
      domain: Taskman.Accounts
    )
    |> case do
      {:ok, token} -> {:ok, token}
      _ -> {:error, :token_generation_failed}
    end
  end

  defp email_change_token(user, email) do
    changeset =
      user
      |> Ash.Changeset.new()
      |> Ash.Changeset.change_attribute(:email, email)

    Confirmation.confirmation_token(
      Info.strategy!(User, :email_change),
      changeset,
      user,
      domain: Taskman.Accounts,
      context: %{taskman: %{administrative_email_token?: true}}
    )
    |> case do
      {:ok, token} -> {:ok, token}
      _ -> {:error, :token_generation_failed}
    end
  end

  defp delete_tokens(user, purposes) do
    Repo.delete_all(
      from token in Token,
        where:
          token.subject == ^AshAuthentication.user_to_subject(user) and token.purpose in ^purposes
    )

    :ok
  rescue
    _exception -> {:error, :token_revocation_failed}
  end

  defp with_delivery(user, :setup, token) do
    Ash.Resource.put_metadata(
      user,
      :managed_email_delivery,
      {:setup, to_string(user.email), token}
    )
  end

  defp with_delivery(user, :email_change, token, email),
    do: Ash.Resource.put_metadata(user, :managed_email_delivery, {:email_change, email, token})

  defp email_changed?(user, email), do: email != String.downcase(to_string(user.email))
  defp normalize_email(email), do: email |> String.trim() |> String.downcase()
end
