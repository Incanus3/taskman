defmodule Taskman.Accounts.SecurityLog do
  @moduledoc """
  Secret-free, structured security event logging.

  Callers pass stable record identifiers only. Metadata is intentionally not
  rendered: authentication material and personal contact details must never
  escape into logs through a future caller.
  """

  require Logger

  @spec record(atom(), keyword()) :: :ok
  def record(event, attrs \\ []) when is_atom(event) and is_list(attrs) do
    ids =
      attrs
      |> Keyword.take([:actor_id, :target_id])
      |> Enum.reject(fn {_key, value} -> is_nil(value) end)
      |> Enum.map_join(" ", fn {key, value} -> "#{key}=#{value}" end)

    suffix = if ids == "", do: "", else: " " <> ids
    Logger.info("security_event=#{event}" <> suffix)
    :ok
  end
end
