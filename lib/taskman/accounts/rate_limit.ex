defmodule Taskman.Accounts.RateLimit do
  @moduledoc """
  Node-local authentication rate limits backed by Hammer ETS.

  The keys deliberately contain only normalized identities or addresses. They
  are never written to logs and the ETS table is discarded when this node
  restarts.
  """

  @behaviour AshRateLimiter.Backend

  @request_ip_key {__MODULE__, :remote_ip}

  @spec child_spec(keyword()) :: Supervisor.child_spec()
  def child_spec(opts) do
    %{
      id: __MODULE__,
      start: {__MODULE__, :start_link, [opts]},
      type: :worker
    }
  end

  @spec start_link(keyword()) :: GenServer.on_start()
  def start_link(opts), do: Taskman.Accounts.RateLimitBackend.start_link(opts)

  @impl AshRateLimiter.Backend
  @spec hit(String.t(), pos_integer(), pos_integer()) ::
          {:allow, pos_integer()} | {:deny, pos_integer()}
  def hit(key, period, limit),
    do: Taskman.Accounts.RateLimitBackend.hit(namespaced_key(key), period, limit)

  @doc false
  @spec expires_at(String.t(), pos_integer()) :: non_neg_integer()
  def expires_at(key, period),
    do: Taskman.Accounts.RateLimitBackend.expires_at(namespaced_key(key), period)

  @type limited_action ::
          :sign_in
          | :password_reset
          | :invitation_resend
          | :email_change_resend
          | :invalid_api_key

  @spec check(limited_action(), keyword()) :: :ok | {:error, retry_after: pos_integer()}
  def check(action, opts) when is_list(opts) do
    backend = Keyword.get(opts, :backend, __MODULE__)

    action
    |> keys(opts)
    |> Enum.reduce_while(:ok, fn {key, period, limit}, :ok ->
      case backend.hit(key, period, limit) do
        {:allow, _count} -> {:cont, :ok}
        {:deny, retry_after_ms} -> {:halt, {:error, retry_after: seconds(retry_after_ms)}}
      end
    end)
  end

  @doc """
  Returns the remaining retry interval for an Ash rate-limit error.

  Hammer's fixed-window ETS backend exposes the current window expiry, whereas
  `AshRateLimiter.LimitExceeded` retains only the configured period. Reading
  that expiry keeps HTTP guidance accurate after the first denied request.
  """
  @spec retry_after(term(), keyword()) :: pos_integer() | nil
  def retry_after(reason, opts \\ [])

  def retry_after(
        %AshRateLimiter.LimitExceeded{backend: backend, key: key, per: period},
        opts
      )
      when is_atom(backend) and is_binary(key) and is_integer(period) and period > 0 do
    now = Keyword.get(opts, :now, System.system_time(:millisecond))

    if function_exported?(backend, :expires_at, 2) do
      backend
      |> apply(:expires_at, [key, period])
      |> Kernel.-(now)
      |> seconds()
      |> min(seconds(period))
    else
      seconds(period)
    end
  end

  def retry_after(%{caused_by: reason}, opts), do: retry_after(reason, opts)

  def retry_after(%{errors: errors}, opts) when is_list(errors) do
    Enum.find_value(errors, &retry_after(&1, opts))
  end

  def retry_after(_reason, _opts), do: nil

  @spec normalized_email(term()) :: String.t()
  def normalized_email(email) when is_binary(email),
    do: email |> String.trim() |> String.downcase()

  def normalized_email(email) do
    if String.Chars.impl_for(email) do
      email
      |> to_string()
      |> normalized_email()
    else
      "unknown"
    end
  end

  @spec normalized_ip(term()) :: String.t()
  def normalized_ip({a, b, c, d} = address)
      when a in 0..255 and b in 0..255 and c in 0..255 and d in 0..255,
      do: address |> :inet.ntoa() |> to_string()

  def normalized_ip(address) when is_tuple(address) do
    address
    |> :inet.ntoa()
    |> to_string()
  rescue
    ArgumentError -> "unknown"
  end

  def normalized_ip(address) when is_binary(address) do
    case :inet.parse_address(String.to_charlist(String.trim(address))) do
      {:ok, parsed} -> normalized_ip(parsed)
      {:error, _reason} -> "unknown"
    end
  end

  def normalized_ip(_address), do: "unknown"

  @doc false
  @spec put_request_remote_ip(term()) :: :ok
  def put_request_remote_ip(remote_ip) do
    Process.put(@request_ip_key, normalized_ip(remote_ip))
    :ok
  end

  @doc false
  @spec request_remote_ip() :: String.t()
  def request_remote_ip, do: Process.get(@request_ip_key, "unknown")

  @doc false
  @spec sign_in_email_key(Ash.Query.t(), map()) :: String.t()
  def sign_in_email_key(query, _context), do: email_key(:sign_in, query_email(query))

  @doc false
  @spec sign_in_ip_key(Ash.Query.t(), map()) :: String.t()
  def sign_in_ip_key(_query, context), do: ip_key(:sign_in, context_ip(context))

  @doc false
  @spec password_reset_email_key(Ash.Query.t(), map()) :: String.t()
  def password_reset_email_key(query, _context),
    do: email_key(:password_reset, query_email(query))

  @doc false
  @spec password_reset_ip_key(Ash.Query.t(), map()) :: String.t()
  def password_reset_ip_key(_query, context), do: ip_key(:password_reset, context_ip(context))

  defp keys(:sign_in, opts) do
    [
      {email_key(:sign_in, Keyword.get(opts, :email)), :timer.minutes(15), 10},
      {ip_key(:sign_in, Keyword.get(opts, :remote_ip)), :timer.minutes(15), 60}
    ]
  end

  defp keys(:password_reset, opts) do
    [
      {ip_key(:password_reset, Keyword.get(opts, :remote_ip)), :timer.hours(1), 20},
      {email_key(:password_reset, Keyword.get(opts, :email)), :timer.hours(1), 5}
    ]
  end

  defp keys(action, opts) when action in [:invitation_resend, :email_change_resend] do
    actor_id = Keyword.get(opts, :actor_id, "unknown") |> to_string()
    email = Keyword.get(opts, :email)
    [{"#{action}:email:#{normalized_email(email)}:actor:#{actor_id}", :timer.hours(1), 5}]
  end

  defp keys(:invalid_api_key, opts) do
    [{ip_key(:invalid_api_key, Keyword.get(opts, :remote_ip)), :timer.minutes(1), 60}]
  end

  defp email_key(action, email), do: "#{action}:email:#{normalized_email(email)}"
  defp ip_key(action, ip), do: "#{action}:ip:#{normalized_ip(ip)}"

  defp query_email(query) do
    query
    |> Map.get(:arguments, %{})
    |> Map.get(:email, "unknown")
  end

  defp context_ip(context) do
    Map.get(context, :remote_ip, Map.get(context, "remote_ip")) ||
      Process.get(@request_ip_key) ||
      get_in(context, [:ash_authentication_request, :remote_ip]) ||
      get_in(context, ["ash_authentication_request", "remote_ip"])
  end

  defp seconds(milliseconds) when is_integer(milliseconds) and milliseconds > 0,
    do: max(1, ceil(milliseconds / 1_000))

  defp seconds(_milliseconds), do: 1

  # The live application deliberately shares one ETS bucket per node. Test
  # requests all originate from Plug.Test's loopback peer, so isolate that
  # otherwise-global state by test process to keep unrelated examples from
  # consuming each other's production-sized budget.
  defp namespaced_key(key) do
    if Application.get_env(:taskman, :rate_limit_test_isolation, false) do
      "test:#{inspect(self())}:#{key}"
    else
      key
    end
  end
end
