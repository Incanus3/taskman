defmodule Taskman.Health do
  @moduledoc """
  Reports whether the application can reach its database.

  The check is intentionally bounded and returns only the states used by the
  public readiness endpoint.
  """

  require Logger

  alias Taskman.Repo

  @default_timeout 750

  @spec check(keyword()) :: :ready | :unavailable
  def check(options \\ []) do
    query = Keyword.get(options, :query, &Repo.query/3)
    timeout = Keyword.get(options, :timeout, @default_timeout)

    try do
      case query.("SELECT 1", [], timeout: timeout) do
        {:ok, _result} -> :ready
        _result -> :unavailable
      end
    rescue
      _exception -> unavailable_with_log()
    catch
      _kind, _reason -> unavailable_with_log()
    end
  end

  defp unavailable_with_log do
    Logger.warning("Taskman health check failed")
    :unavailable
  end
end
