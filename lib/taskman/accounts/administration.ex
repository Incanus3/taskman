defmodule Taskman.Accounts.Administration do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.User
  alias Taskman.Repo

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
end
