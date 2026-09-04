defmodule Taskman.Accounts.Token.Persistence do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.{Token, User}
  alias Taskman.Repo

  @doc false
  @spec list_browser_sessions(User.t(), DateTime.t()) :: [map()]
  def list_browser_sessions(%User{} = user, %DateTime{} = now) do
    subject = AshAuthentication.user_to_subject(user)

    Repo.all(
      from token in Token,
        where: token.subject == ^subject and token.purpose == "user" and token.expires_at > ^now,
        order_by: [desc: token.created_at, desc: token.jti]
    )
    |> Enum.map(fn token ->
      %{jti: token.jti, created_at: token.created_at, expires_at: token.expires_at}
    end)
  end

  def list_browser_sessions(_user, _now), do: []

  @doc false
  @spec browser_session_exists?(User.t(), String.t()) :: boolean()
  def browser_session_exists?(%User{} = user, jti) when is_binary(jti) do
    subject = AshAuthentication.user_to_subject(user)

    Repo.exists?(
      from token in Token,
        where: token.subject == ^subject and token.purpose == "user" and token.jti == ^jti
    )
  end

  def browser_session_exists?(_user, _jti), do: false

  @doc false
  @spec pending_email_change(User.t(), DateTime.t()) :: String.t() | nil
  def pending_email_change(%User{} = user, %DateTime{} = now) do
    subject = AshAuthentication.user_to_subject(user)

    Repo.one(
      from token in Token,
        where:
          token.subject == ^subject and token.purpose == "email_change" and
            token.expires_at > ^now,
        order_by: [desc: token.created_at],
        limit: 1,
        select: token.extra_data
    )
    |> case do
      %{"email" => email} when is_binary(email) -> email
      _ -> nil
    end
  end

  def pending_email_change(_user, _now), do: nil

  @doc false
  @spec delete_for_subject(Taskman.Accounts.User.t(), [String.t()] | nil) ::
          {:ok, [{String.t(), String.t()}]} | {:error, :token_deletion_failed}
  def delete_for_subject(user, purposes) when is_list(purposes) or is_nil(purposes) do
    query =
      from token in Token,
        where: token.subject == ^AshAuthentication.user_to_subject(user)

    query = if purposes, do: from(token in query, where: token.purpose in ^purposes), else: query

    {_count, deleted_tokens} =
      Repo.delete_all(
        from token in query,
          select: {token.jti, token.purpose}
      )

    {:ok, deleted_tokens}
  rescue
    _exception -> {:error, :token_deletion_failed}
  end
end
