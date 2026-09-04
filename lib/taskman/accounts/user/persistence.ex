defmodule Taskman.Accounts.User.Persistence do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.User
  alias Taskman.Repo

  @doc false
  @spec lock(Ecto.UUID.t() | term()) :: {:ok, User.t()} | :error
  def lock(user_id) when is_binary(user_id) do
    case Repo.one(from user in User, where: user.id == ^user_id, lock: "FOR UPDATE") do
      %User{} = user -> {:ok, user}
      nil -> :error
    end
  end

  def lock(_user_id), do: :error

  @doc false
  @spec lock_eligible_by_email(String.t() | term()) :: {:ok, User.t()} | :error
  def lock_eligible_by_email(email) when is_binary(email) do
    case Repo.one(
           from user in User,
             where:
               user.email == ^email and user.status == :active and not is_nil(user.confirmed_at),
             lock: "FOR UPDATE"
         ) do
      %User{} = user -> {:ok, user}
      nil -> :error
    end
  end

  def lock_eligible_by_email(_email), do: :error

  @doc false
  @spec update_email(User.t(), map()) :: {:ok, User.t()} | {:error, Ecto.Changeset.t()}
  def update_email(%User{} = user, attributes) when is_map(attributes) do
    user
    |> Ecto.Changeset.cast(attributes, [:email, :confirmed_at])
    |> Ecto.Changeset.unique_constraint(:email)
    |> Repo.update()
  end
end
