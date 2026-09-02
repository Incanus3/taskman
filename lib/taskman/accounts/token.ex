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
  @socket_topic_prefix "taskman_sessions:"

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
  @spec browser_session_topics(Taskman.Accounts.User.t(), keyword()) ::
          {:ok, [String.t()]} | {:error, term()}
  def browser_session_topics(user, opts \\ []) do
    excluded_jti = Keyword.get(opts, :except_jti)

    with {:ok, tokens} <- tokens_for_subject(user, "user") do
      topics =
        tokens
        |> Enum.reject(fn token -> token.jti == excluded_jti end)
        |> Enum.map(&session_topic_for_jti(&1.jti))

      {:ok, topics}
    end
  end

  @doc """
  Returns the LiveView socket identity associated with a stored browser token.

  Browser sessions are identified by their token JTI rather than by the complete
  credential. This keeps the PubSub topic bounded while still isolating every
  browser session from the user's other sessions.
  """
  @spec session_topic(String.t()) :: String.t()
  def session_topic(token) when is_binary(token) do
    case Jwt.peek(token) do
      {:ok, %{"jti" => jti}} when is_binary(jti) -> session_topic_for_jti(jti)
      _ -> @socket_topic_prefix <> Base.url_encode64(token)
    end
  end

  @spec session_topic_for_jti(String.t()) :: String.t()
  def session_topic_for_jti(jti) when is_binary(jti),
    do: @socket_topic_prefix <> Base.url_encode64(jti)

  @doc false
  @spec broadcast_disconnect(String.t()) :: :ok | {:error, term()}
  def broadcast_disconnect(topic) when is_binary(topic) do
    Phoenix.Channel.Server.broadcast(Taskman.PubSub, topic, "disconnect", %{})
  end

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

  @spec revoke_for_subject(Taskman.Accounts.User.t(), String.t(), keyword()) ::
          :ok | {:error, term()}
  def revoke_for_subject(user, purpose, opts \\ []) do
    with {:ok, tokens} <- tokens_for_subject(user, purpose) do
      revoke_tokens(
        tokens,
        Keyword.put_new(opts, :disconnect?, purpose in @browser_session_purposes)
      )
    end
  end

  @spec revoke_for_subject_except(Taskman.Accounts.User.t(), String.t(), String.t(), keyword()) ::
          :ok | {:error, term()}
  def revoke_for_subject_except(user, purpose, excluded_jti, opts \\ []) do
    with {:ok, tokens} <- tokens_for_subject(user, purpose) do
      tokens
      |> Enum.reject(&(&1.jti == excluded_jti))
      |> revoke_tokens(Keyword.put_new(opts, :disconnect?, purpose in @browser_session_purposes))
    end
  end

  @spec revoke_all_for_subject(Taskman.Accounts.User.t()) :: :ok | {:error, term()}
  def revoke_all_for_subject(user) do
    with {:ok, tokens} <- tokens_for_subject(user, nil) do
      with :ok <- revoke_tokens(tokens, disconnect?: false) do
        tokens
        |> Enum.filter(&(&1.purpose in @browser_session_purposes))
        |> Enum.map(&session_topic_for_jti(&1.jti))
        |> broadcast_topics()
      end
    end
  end

  @doc false
  @spec revoke_jti(String.t(), String.t(), keyword()) :: :ok | {:error, term()}
  def revoke_jti(jti, subject, opts \\ [])
      when is_binary(jti) and is_binary(subject) and is_list(opts) do
    with :ok <- TokenResource.Actions.revoke_jti(__MODULE__, jti, subject),
         true <- Keyword.get(opts, :disconnect?, false) do
      broadcast_disconnect(session_topic_for_jti(jti))
    else
      false -> :ok
      {:error, _reason} = error -> error
      _ -> {:error, :token_revocation_failed}
    end
  end

  defp revoke_tokens(tokens, opts) do
    disconnect? = Keyword.get(opts, :disconnect?, false)

    Enum.reduce_while(tokens, {:ok, []}, fn token, {:ok, revoked} ->
      case TokenResource.Actions.revoke_jti(__MODULE__, token.jti, token.subject) do
        :ok -> {:cont, {:ok, [token.jti | revoked]}}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
    |> case do
      {:ok, jtis} when disconnect? ->
        jtis
        |> Enum.map(&session_topic_for_jti/1)
        |> broadcast_topics()

      {:ok, _jtis} ->
        :ok

      {:error, _reason} = error ->
        error
    end
  end

  defp tokens_for_subject(user, purpose) do
    query =
      __MODULE__
      |> Ash.Query.new()
      |> Ash.Query.filter(subject: AshAuthentication.user_to_subject(user))
      |> Ash.Query.select([:jti, :subject, :purpose])
      |> Ash.Query.set_context(%{private: %{ash_authentication?: true}})

    query = if purpose, do: Ash.Query.filter(query, purpose: purpose), else: query

    Ash.read(query, domain: Accounts)
  end

  defp broadcast_topics(topics) do
    Enum.reduce_while(topics, :ok, fn topic, :ok ->
      case broadcast_disconnect(topic) do
        :ok -> {:cont, :ok}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
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
