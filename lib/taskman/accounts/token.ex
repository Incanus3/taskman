defmodule Taskman.Accounts.Token do
  use Ash.Resource,
    data_layer: AshPostgres.DataLayer,
    extensions: [AshAuthentication.TokenResource],
    authorizers: [Ash.Policy.Authorizer],
    domain: Taskman.Accounts

  require Ash.Query

  import Ecto.Query, only: [from: 2]

  postgres do
    table "tokens"
    repo Taskman.Repo
  end

  actions do
    defaults [:read]
  end

  alias AshAuthentication.{Jwt, TokenResource}
  alias Taskman.Accounts
  alias Taskman.Repo

  @browser_session_purposes ["user"]

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

  @doc false
  @spec claim_for_redemption(String.t(), String.t(), DateTime.t()) ::
          {:ok, t()} | {:error, :invalid_token}
  def claim_for_redemption(token, purpose, now \\ DateTime.utc_now()) do
    with {:ok, %{"jti" => jti}} <- Jwt.peek(token),
         %__MODULE__{} = stored_token <-
           Repo.one(
             from stored_token in __MODULE__,
               where:
                 stored_token.jti == ^jti and stored_token.purpose == ^purpose and
                   stored_token.expires_at > ^now,
               lock: "FOR UPDATE"
           ) do
      {:ok, stored_token}
    else
      _ -> {:error, :invalid_token}
    end
  end

  @doc false
  @spec browser_session_purposes() :: [String.t()]
  def browser_session_purposes, do: @browser_session_purposes

  @doc false
  @spec revoke_browser_sessions(Taskman.Accounts.User.t(), keyword()) :: :ok | {:error, term()}
  def revoke_browser_sessions(user, opts \\ []) do
    case Keyword.get(opts, :session_revoker) do
      nil ->
        @browser_session_purposes
        |> Enum.reduce_while(:ok, fn purpose, :ok ->
          case revoke_for_subject(user, purpose) do
            :ok -> {:cont, :ok}
            {:error, reason} -> {:halt, {:error, reason}}
          end
        end)

      revoker when is_function(revoker, 1) ->
        case revoker.(user) do
          :ok -> :ok
          {:error, _reason} = error -> error
          _ -> {:error, :invalid_session_revoker}
        end

      _ ->
        {:error, :invalid_session_revoker}
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
