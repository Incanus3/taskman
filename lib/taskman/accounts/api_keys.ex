defmodule Taskman.Accounts.ApiKeys do
  @moduledoc false

  require Ash.Query

  alias Taskman.Accounts.{ApiKey, Authentication, SecurityLog, User}

  @api_key_lifetime_days 365
  @api_key_minimum_lifetime_seconds 86_400
  @api_key_lifetime_seconds @api_key_lifetime_days * 86_400

  @spec create_api_key(User.t(), map()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs) when is_map(attrs), do: create_api_key(actor, attrs, [])

  @spec create_api_key(User.t(), map(), keyword()) ::
          {:ok, %{api_key: ApiKey.t(), plaintext: String.t()}} | {:error, term()}
  def create_api_key(actor, attrs, opts) when is_map(attrs) and is_list(opts) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor),
           {:ok, name} <- api_key_name(attrs),
           {:ok, expires_at} <- api_key_expiration(attrs, opts),
           {:ok, api_key} <-
             ApiKey
             |> Ash.Changeset.for_create(:create_for_user, %{
               name: name,
               expires_at: expires_at,
               user_id: actor.id
             })
             |> Ash.create(actor: actor, domain: Taskman.Accounts),
           plaintext when is_binary(plaintext) <-
             Ash.Resource.get_metadata(api_key, :plaintext_api_key) do
        {:ok, %{api_key: api_key, plaintext: plaintext}}
      else
        nil -> {:error, :api_key_generation_failed}
        {:error, _reason} = error -> error
        _ -> {:error, :invalid_input}
      end

    SecurityLog.audit(result, :api_key_created, :api_key_creation_rejected, actor, actor)
  end

  def create_api_key(actor, _attrs, _opts) do
    result = {:error, :invalid_input}
    SecurityLog.audit(result, :api_key_created, :api_key_creation_rejected, actor, actor)
  end

  @spec list_api_keys(User.t()) :: {:ok, [ApiKey.t()]} | {:error, term()}
  def list_api_keys(actor) do
    with {:ok, actor} <- Authentication.current_api_key_actor(actor),
         {:ok, keys} <- list_owned_api_keys(actor) do
      {:ok, keys}
    end
  end

  @spec revoke_api_key(User.t(), Ecto.UUID.t()) :: :ok | {:error, term()}
  def revoke_api_key(actor, id) do
    result =
      with {:ok, actor} <- Authentication.current_api_key_actor(actor),
           {:ok, uuid} <- Ecto.UUID.cast(id),
           {:ok, api_key} <- fetch_owned_api_key(actor, uuid),
           {:ok, _revoked} <-
             api_key
             |> Ash.Changeset.for_update(:revoke, %{})
             |> Ash.update(actor: actor, domain: Taskman.Accounts) do
        :ok
      else
        :error -> {:error, :not_found}
        {:error, _reason} = error -> error
      end

    SecurityLog.audit(result, :api_key_revoked, :api_key_revocation_rejected, actor, actor)
  end

  defp api_key_name(attrs) do
    case Map.get(attrs, :name, Map.get(attrs, "name")) do
      name when is_binary(name) ->
        name = String.trim(name)
        if name == "", do: {:error, :invalid_name}, else: {:ok, name}

      _ ->
        {:error, :invalid_name}
    end
  end

  defp api_key_expiration(attrs, opts) do
    now = Keyword.get(opts, :now, DateTime.utc_now())

    case Map.fetch(attrs, :expires_at) do
      :error ->
        case Map.fetch(attrs, "expires_at") do
          :error -> {:error, :invalid_expiration}
          {:ok, value} -> validate_api_key_expiration(value, now)
        end

      {:ok, value} ->
        validate_api_key_expiration(value, now)
    end
  end

  defp validate_api_key_expiration(%DateTime{} = expires_at, %DateTime{} = now) do
    minimum_expiry = DateTime.add(now, @api_key_minimum_lifetime_seconds, :second)
    max_expiry = DateTime.add(now, @api_key_lifetime_seconds, :second)

    if DateTime.compare(expires_at, minimum_expiry) != :lt and
         DateTime.compare(expires_at, max_expiry) != :gt do
      {:ok, expires_at}
    else
      {:error, :invalid_expiration}
    end
  end

  defp validate_api_key_expiration(_expires_at, _now), do: {:error, :invalid_expiration}

  defp fetch_owned_api_key(actor, id) do
    query =
      ApiKey
      |> Ash.Query.for_read(:get_for_user, %{}, actor: actor, domain: Taskman.Accounts)
      |> Ash.Query.filter(id: id)

    case Ash.read_one(query, actor: actor, domain: Taskman.Accounts) do
      {:ok, %ApiKey{} = api_key} -> {:ok, api_key}
      {:ok, nil} -> {:error, :not_found}
      {:error, _reason} = error -> error
    end
  end

  defp list_owned_api_keys(actor) do
    query =
      ApiKey
      |> Ash.Query.for_read(:list_for_user, %{}, actor: actor, domain: Taskman.Accounts)
      |> Ash.Query.sort(inserted_at: :asc, id: :asc)

    Ash.read(query, actor: actor, domain: Taskman.Accounts)
  end
end
