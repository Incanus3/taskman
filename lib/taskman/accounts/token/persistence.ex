defmodule Taskman.Accounts.Token.Persistence do
  @moduledoc false

  import Ecto.Query, only: [from: 2]

  alias Taskman.Accounts.Token
  alias Taskman.Repo

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
