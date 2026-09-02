defmodule Taskman.Accounts.Token do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    extensions: [AshAuthentication.TokenResource],
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  require Ash.Query

  postgres do
    table "tokens"
    repo Taskman.Repo
  end

  actions do
    defaults [:read]
  end

  alias AshAuthentication.{Jwt, TokenResource}
  alias Taskman.Accounts

  @spec valid_for_purpose?(String.t(), String.t(), DateTime.t()) :: :ok | {:error, :invalid_token}
  def valid_for_purpose?(token, purpose, now \\ DateTime.utc_now()) do
    with {:ok, %{"jti" => _jti}} <- Jwt.peek(token),
         {:ok, [stored_token]} <-
           TokenResource.Actions.get_token(__MODULE__, %{token: token, purpose: purpose}),
         :gt <- DateTime.compare(stored_token.expires_at, now) do
      :ok
    else
      _ -> {:error, :invalid_token}
    end
  end

  @spec revoke_for_subject(Taskman.Accounts.User.t(), String.t()) :: :ok | {:error, term()}
  def revoke_for_subject(user, purpose) do
    with {:ok, tokens} <- tokens_for_subject(user, purpose) do
      revoke_tokens(tokens)
    end
  end

  @spec revoke_for_subject_except(Taskman.Accounts.User.t(), String.t(), String.t()) ::
          :ok | {:error, term()}
  def revoke_for_subject_except(user, purpose, excluded_jti) do
    with {:ok, tokens} <- tokens_for_subject(user, purpose) do
      tokens
      |> Enum.reject(&(&1.jti == excluded_jti))
      |> revoke_tokens()
    end
  end

  @spec revoke_all_for_subject(Taskman.Accounts.User.t()) :: :ok | {:error, term()}
  def revoke_all_for_subject(user) do
    with {:ok, tokens} <- tokens_for_subject(user, nil) do
      revoke_tokens(tokens)
    end
  end

  defp revoke_tokens(tokens) do
    Enum.reduce_while(tokens, :ok, fn token, :ok ->
      case TokenResource.Actions.revoke_jti(__MODULE__, token.jti, token.subject) do
        :ok -> {:cont, :ok}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
  end

  defp tokens_for_subject(user, purpose) do
    query =
      __MODULE__
      |> Ash.Query.new()
      |> Ash.Query.filter(subject: AshAuthentication.user_to_subject(user))
      |> Ash.Query.select([:jti, :subject])
      |> Ash.Query.set_context(%{private: %{ash_authentication?: true}})

    query = if purpose, do: Ash.Query.filter(query, purpose: purpose), else: query

    Ash.read(query, domain: Accounts)
  end

  policies do
    bypass AshAuthentication.Checks.AshAuthenticationInteraction do
      authorize_if always()
    end

    policy always() do
      forbid_if always()
    end
  end
end
