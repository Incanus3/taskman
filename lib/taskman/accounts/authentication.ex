defmodule Taskman.Accounts.Authentication do
  @moduledoc false

  require Ash.Query

  alias AshAuthentication.{Errors.AuthenticationFailed, Info, Jwt, Strategy}
  alias Taskman.Accounts.{ApiKey, SecurityLog, User}

  @api_key_digest_bytes 32
  @dummy_api_key_digest :crypto.hash(:sha256, "taskman-api-key-dummy")

  @spec sign_in_with_password(map()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_password(params) when is_map(params) do
    result =
      User
      |> Info.strategy!(:password)
      |> Strategy.action(:sign_in, params, domain: Taskman.Accounts)

    SecurityLog.audit(result, :sign_in_succeeded, :sign_in_rejected, nil, result_user(result))
  end

  def sign_in_with_password(_params) do
    result = {:error, :invalid_credentials}
    SecurityLog.audit(result, :sign_in_succeeded, :sign_in_rejected)
  end

  @spec sign_in_with_api_key(map()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params) when is_map(params), do: sign_in_with_api_key(params, [])

  @spec sign_in_with_api_key(map(), keyword()) :: {:ok, User.t()} | {:error, term()}
  def sign_in_with_api_key(params, opts) when is_map(params) and is_list(opts) do
    with {:ok, api_key} <- api_key_param(params),
         {:ok, user} <- authenticate_api_key(api_key),
         true <- eligible?(user) do
      {:ok, user}
    else
      {:error, _reason} -> {:error, authentication_failed()}
      false -> {:error, authentication_failed()}
    end
  end

  def sign_in_with_api_key(_params, _opts) do
    _ = compare_dummy_api_key("")
    {:error, authentication_failed()}
  end

  @doc false
  @spec current_api_key_actor(User.t() | term()) ::
          {:ok, User.t()} | {:error, :authentication_required}
  def current_api_key_actor(%User{} = actor) do
    query =
      User
      |> Ash.Query.for_read(:api_key_actor, %{},
        actor: actor,
        domain: Taskman.Accounts
      )

    case Ash.read_one(query, actor: actor, domain: Taskman.Accounts) do
      {:ok, %User{} = current_actor} -> {:ok, current_actor}
      {:ok, nil} -> {:error, :authentication_required}
      {:error, _reason} -> {:error, :authentication_required}
    end
  end

  def current_api_key_actor(_actor), do: {:error, :authentication_required}

  @doc false
  @spec token_user(String.t()) :: {:ok, User.t()} | {:error, :invalid_token}
  def token_user(token) when is_binary(token) do
    with {:ok, %{"sub" => subject}, _resource} <- Jwt.verify(token, User),
         {:ok, user} <-
           AshAuthentication.subject_to_user(subject, User, domain: Taskman.Accounts) do
      {:ok, user}
    else
      _ -> {:error, :invalid_token}
    end
  end

  def token_user(_token), do: {:error, :invalid_token}

  @doc false
  @spec eligible?(User.t() | term()) :: boolean()
  def eligible?(%User{} = user), do: user.status == :active and not is_nil(user.confirmed_at)
  def eligible?(_user), do: false

  @doc false
  @spec authentication_failed() :: Exception.t()
  def authentication_failed do
    AuthenticationFailed.exception(caused_by: %{module: Taskman.Accounts, action: :sign_in})
  end

  defp api_key_param(params) do
    case Map.get(params, :api_key, Map.get(params, "api_key")) do
      api_key when is_binary(api_key) ->
        {:ok, api_key}

      _ ->
        _ = compare_dummy_api_key("")
        {:error, :invalid_credentials}
    end
  end

  defp authenticate_api_key(api_key) when is_binary(api_key) do
    case canonical_api_key_id(api_key) do
      {:ok, id} ->
        query =
          ApiKey
          |> Ash.Query.for_read(:for_authentication, %{},
            domain: Taskman.Accounts,
            context: authentication_context()
          )
          |> Ash.Query.filter(id: id)
          |> Ash.Query.load(user: Ash.Query.set_context(User, authentication_context()))

        case Ash.read_one(query, domain: Taskman.Accounts) do
          {:ok, %ApiKey{user: %User{} = user} = api_key_record} ->
            if api_key_matches?(api_key, api_key_record.api_key_hash) do
              {:ok, user}
            else
              {:error, :invalid_credentials}
            end

          {:ok, nil} ->
            _ = compare_dummy_api_key(api_key)
            {:error, :invalid_credentials}

          {:error, _reason} ->
            _ = compare_dummy_api_key(api_key)
            {:error, :invalid_credentials}
        end

      :error ->
        _ = compare_dummy_api_key(api_key)
        {:error, :invalid_credentials}
    end
  end

  defp canonical_api_key_id(api_key) do
    with ["tm", middle, checksum] <- String.split(api_key, "_", parts: 3),
         {:ok, payload} <- AshAuthentication.Base.bindecode62(middle),
         true <- payload != <<>> and :binary.first(payload) != 0,
         true <- AshAuthentication.Base.encode62(payload) == middle,
         <<_random_bytes::binary-size(32), id::binary-size(16)>> <- payload,
         {:ok, checksum_value} <- AshAuthentication.Base.decode62(checksum),
         true <- AshAuthentication.Base.encode62(checksum_value) == checksum,
         true <- checksum_value == :erlang.crc32(payload) do
      {:ok, id}
    else
      _ -> :error
    end
  end

  defp api_key_matches?(api_key, stored_hash) when is_binary(api_key) do
    digest = :crypto.hash(:sha256, api_key)

    candidate =
      if is_binary(stored_hash) and byte_size(stored_hash) == @api_key_digest_bytes,
        do: stored_hash,
        else: @dummy_api_key_digest

    Plug.Crypto.secure_compare(digest, candidate)
  end

  defp compare_dummy_api_key(api_key) when is_binary(api_key) do
    Plug.Crypto.secure_compare(:crypto.hash(:sha256, api_key), @dummy_api_key_digest)
  end

  defp authentication_context, do: %{private: %{ash_authentication?: true}}

  defp result_user({:ok, %User{} = user}), do: user
  defp result_user({:ok, %{user: %User{} = user}}), do: user
  defp result_user(_result), do: nil
end
