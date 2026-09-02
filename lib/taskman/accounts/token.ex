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
  alias Taskman.Accounts.User
  alias Taskman.Repo

  @browser_session_purposes ["user"]
  @socket_topic_prefix "taskman_sessions:"

  @spec valid_for_purpose?(String.t(), String.t(), DateTime.t()) :: :ok | {:error, :invalid_token}
  def valid_for_purpose?(token, purpose, now \\ DateTime.utc_now()) do
    with {:ok, _stored_token, _claims} <- verified_stored_token(token, purpose, now, false) do
      :ok
    else
      _ -> {:error, :invalid_token}
    end
  end

  @doc false
  @spec claim_for_redemption(String.t(), String.t(), DateTime.t()) ::
          {:ok, t()} | {:error, :invalid_token}
  def claim_for_redemption(token, purpose, now \\ DateTime.utc_now()) do
    with {:ok, stored_token, _claims} <- verified_stored_token(token, purpose, now, true) do
      {:ok, stored_token}
    else
      _ -> {:error, :invalid_token}
    end
  end

  @doc false
  @spec claim_browser_session(String.t(), DateTime.t()) ::
          {:ok, t(), map()} | {:error, :invalid_token}
  def claim_browser_session(token, now \\ DateTime.utc_now()) do
    verified_stored_token(token, "user", now, true)
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
        with {:ok, jtis} <- revoke_browser_sessions_with_jtis(user, opts) do
          broadcast_session_jtis(jtis)
        end

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

  @doc false
  @spec revoke_browser_sessions_with_jtis(Taskman.Accounts.User.t(), keyword()) ::
          {:ok, [String.t()]} | {:error, term()}
  def revoke_browser_sessions_with_jtis(user, opts \\ []) do
    case Keyword.get(opts, :session_revoker) do
      nil ->
        transact(fn ->
          @browser_session_purposes
          |> Enum.reduce_while({:ok, []}, fn purpose, {:ok, revoked} ->
            with {:ok, tokens} <- tokens_for_subject(user, purpose),
                 {:ok, jtis} <- revoke_tokens(tokens) do
              {:cont, {:ok, revoked ++ jtis}}
            else
              {:error, reason} -> {:halt, {:error, reason}}
            end
          end)
        end)

      revoker when is_function(revoker, 1) ->
        case revoker.(user) do
          :ok -> {:ok, []}
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
    with {:ok, jtis} <- revoke_for_subject_with_jtis(user, purpose, opts) do
      if Keyword.get(opts, :disconnect?, purpose in @browser_session_purposes) do
        broadcast_session_jtis(jtis)
      else
        :ok
      end
    end
  end

  @doc false
  @spec revoke_for_subject_with_jtis(Taskman.Accounts.User.t(), String.t(), keyword()) ::
          {:ok, [String.t()]} | {:error, term()}
  def revoke_for_subject_with_jtis(user, purpose, _opts \\ []) do
    transact(fn ->
      with {:ok, tokens} <- tokens_for_subject(user, purpose),
           {:ok, jtis} <- revoke_tokens(tokens) do
        {:ok, jtis}
      end
    end)
  end

  @spec revoke_for_subject_except(Taskman.Accounts.User.t(), String.t(), String.t(), keyword()) ::
          :ok | {:error, term()}
  def revoke_for_subject_except(user, purpose, excluded_jti, opts \\ []) do
    with {:ok, jtis} <- revoke_for_subject_except_with_jtis(user, purpose, excluded_jti, opts) do
      if Keyword.get(opts, :disconnect?, purpose in @browser_session_purposes) do
        broadcast_session_jtis(jtis)
      else
        :ok
      end
    end
  end

  @doc false
  @spec revoke_for_subject_except_with_jtis(
          Taskman.Accounts.User.t(),
          String.t(),
          String.t(),
          keyword()
        ) :: {:ok, [String.t()]} | {:error, term()}
  def revoke_for_subject_except_with_jtis(user, purpose, excluded_jti, _opts \\ []) do
    transact(fn ->
      with {:ok, tokens} <- tokens_for_subject(user, purpose),
           tokens <- Enum.reject(tokens, &(&1.jti == excluded_jti)),
           {:ok, jtis} <- revoke_tokens(tokens) do
        {:ok, jtis}
      end
    end)
  end

  @spec revoke_all_for_subject(Taskman.Accounts.User.t()) :: :ok | {:error, term()}
  def revoke_all_for_subject(user) do
    with {:ok, entries} <- revoke_all_for_subject_with_jtis(user) do
      entries
      |> Enum.filter(fn {_jti, purpose} -> purpose in @browser_session_purposes end)
      |> Enum.map(&elem(&1, 0))
      |> broadcast_session_jtis()
    end
  end

  @doc false
  @spec revoke_all_for_subject_with_jtis(Taskman.Accounts.User.t()) ::
          {:ok, [{String.t(), String.t()}]} | {:error, term()}
  def revoke_all_for_subject_with_jtis(user) do
    transact(fn ->
      with {:ok, tokens} <- tokens_for_subject(user, nil),
           {:ok, _jtis} <- revoke_tokens(tokens) do
        {:ok, Enum.map(tokens, &{&1.jti, &1.purpose})}
      end
    end)
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

  @doc false
  @spec revoke_token(String.t()) :: :ok | {:error, term()}
  def revoke_token(token) when is_binary(token) do
    TokenResource.Actions.revoke(__MODULE__, token)
  end

  @doc false
  @spec establish_browser_session(Taskman.Accounts.User.t(), String.t(), String.t() | nil) ::
          {:ok, [String.t()]} | {:error, term()}
  def establish_browser_session(user, token, previous_token)
      when is_struct(user) and is_binary(token) do
    subject = AshAuthentication.user_to_subject(user)

    case Ash.transact([__MODULE__], fn ->
           with {:ok, new_record, %{"sub" => ^subject}} <- claim_browser_session(token),
                {:ok, retired_jtis} <-
                  retire_previous_session(previous_token, subject, new_record.jti) do
             {:ok, retired_jtis}
           else
             {:ok, _record, _claims} -> {:error, :invalid_session}
             {:error, _reason} = error -> error
             _ -> {:error, :invalid_session}
           end
         end) do
      {:ok, {:ok, retired_jtis}} -> {:ok, retired_jtis}
      {:error, _reason} = error -> error
      _ -> {:error, :session_establishment_failed}
    end
  end

  defp retire_previous_session(nil, _subject, _new_jti), do: {:ok, []}

  defp retire_previous_session(previous_token, _subject, new_jti)
       when is_binary(previous_token) do
    case claim_browser_session(previous_token) do
      {:ok, _previous_record, %{"sub" => previous_subject, "jti" => previous_jti}}
      when is_binary(previous_subject) and previous_jti != new_jti ->
        with :ok <- revoke_jti(previous_jti, previous_subject, disconnect?: false) do
          {:ok, [previous_jti]}
        end

      {:error, :invalid_token} ->
        {:ok, []}

      _ ->
        {:error, :invalid_session}
    end
  end

  @doc false
  @spec broadcast_session_jtis([String.t()]) :: :ok | {:error, term()}
  def broadcast_session_jtis(jtis) when is_list(jtis) do
    jtis
    |> Enum.map(&session_topic_for_jti/1)
    |> broadcast_topics()
  end

  defp revoke_tokens(tokens) do
    Enum.reduce_while(tokens, {:ok, []}, fn token, {:ok, revoked} ->
      case TokenResource.Actions.revoke_jti(__MODULE__, token.jti, token.subject) do
        :ok -> {:cont, {:ok, [token.jti | revoked]}}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
  end

  defp verified_stored_token(token, purpose, now, lock?) do
    with {:ok, claims, _resource} <- Jwt.verify(token, User),
         %{"jti" => jti, "sub" => subject}
         when is_binary(jti) and is_binary(subject) <- claims,
         true <- purpose_claim_matches?(claims, purpose),
         %__MODULE__{} = stored_token <-
           stored_token_for_jti(jti, purpose, subject, now, lock?) do
      {:ok, stored_token, claims}
    else
      _ -> {:error, :invalid_token}
    end
  end

  defp purpose_claim_matches?(claims, purpose) do
    Map.get(claims, "purpose") in [nil, purpose]
  end

  defp stored_token_for_jti(jti, purpose, subject, now, lock?) do
    query =
      from stored_token in __MODULE__,
        where:
          stored_token.jti == ^jti and stored_token.purpose == ^purpose and
            stored_token.subject == ^subject and
            stored_token.expires_at > ^now

    query = if lock?, do: Ecto.Query.lock(query, "FOR UPDATE"), else: query
    Repo.one(query)
  end

  defp transact(operation) do
    case Ash.transact([__MODULE__], operation) do
      {:ok, {:ok, value}} -> {:ok, value}
      {:error, _reason} = error -> error
      _ -> {:error, :token_revocation_failed}
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
