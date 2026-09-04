defmodule Taskman.Accounts.Token.Changes.GuardIssuance do
  @moduledoc false

  use Ash.Resource.Change

  alias Taskman.Accounts.User
  alias Taskman.Accounts.User.Persistence

  @impl true
  def change(changeset, _opts, _context) do
    case get_in(changeset.context, [:ash_authentication, :user]) do
      %User{id: user_id} ->
        Ash.Changeset.before_action(changeset, fn changeset ->
          with {:ok, %User{} = user} <- Persistence.lock(user_id),
               true <- eligible_for_token?(user, changeset) do
            if password_credential_current?(changeset, user) do
              changeset
            else
              # The password strategy expects token storage to succeed after it has verified a
              # password. A stale verification is stored as a non-authenticating record instead
              # of issuing a browser-session token.
              Ash.Changeset.force_change_attribute(changeset, :purpose, "revocation")
            end
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

  defp password_credential_current?(changeset, %User{} = current_user) do
    case {browser_session_token?(changeset),
          get_in(changeset.context, [:ash_authentication, :user])} do
      {true, %User{hashed_password: issued_password_hash}}
      when is_binary(issued_password_hash) and is_binary(current_user.hashed_password) ->
        issued_password_hash == current_user.hashed_password

      _ ->
        true
    end
  end

  defp setup_token?(changeset) do
    case AshAuthentication.Jwt.peek(Ash.Changeset.get_argument(changeset, :token)) do
      {:ok, %{"act" => "complete_setup"}} -> true
      _ -> false
    end
  end
end
