defmodule Taskman.Accounts.Changes.GuardTokenIssuance do
  @moduledoc false

  use Ash.Resource.Change

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.User
  alias Taskman.Repo

  @impl true
  def change(changeset, _opts, _context) do
    case get_in(changeset.context, [:ash_authentication, :user]) do
      %User{id: user_id} ->
        Ash.Changeset.before_action(changeset, fn changeset ->
          with %User{} = user <-
                 Repo.one(from user in User, where: user.id == ^user_id, lock: "FOR UPDATE"),
               true <- eligible_for_token?(user, changeset) do
            changeset
          else
            _ ->
              Ash.Changeset.add_error(changeset, field: :token, message: "cannot issue a token")
          end
        end)

      _ ->
        changeset
    end
  end

  defp eligible_for_token?(%User{status: :disabled}, changeset),
    do: get_in(changeset.context, [:taskman, :administrative_email_token?]) == true

  defp eligible_for_token?(%User{status: :active, confirmed_at: %DateTime{}}, _changeset),
    do: true

  defp eligible_for_token?(%User{status: :active}, changeset),
    do: not browser_session_token?(changeset)

  defp eligible_for_token?(%User{status: :pending}, changeset), do: setup_token?(changeset)

  defp eligible_for_token?(_user, _changeset), do: false

  defp browser_session_token?(changeset) do
    Ash.Changeset.get_attribute(changeset, :purpose) == "user" and
      case AshAuthentication.Jwt.peek(Ash.Changeset.get_argument(changeset, :token)) do
        {:ok, claims} -> not Map.has_key?(claims, "act")
        _ -> true
      end
  end

  defp setup_token?(changeset) do
    case AshAuthentication.Jwt.peek(Ash.Changeset.get_argument(changeset, :token)) do
      {:ok, %{"act" => "complete_setup"}} -> true
      _ -> false
    end
  end
end
