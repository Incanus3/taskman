defmodule Taskman.Accounts.SecurityLog do
  @moduledoc """
  Secret-free, structured security event logging.

  Callers pass stable record identifiers only. Metadata is intentionally not
  rendered: authentication material and personal contact details must never
  escape into logs through a future caller.
  """

  require Logger

  alias Taskman.Accounts.User

  @doc false
  @spec audit(term(), atom(), atom(), User.t() | term(), User.t() | term()) :: term()
  def audit(result, success_event, rejection_event, actor \\ nil, target \\ nil) do
    event = if successful?(result), do: success_event, else: rejection_event

    record(event,
      actor_id: record_id(actor),
      target_id: record_id(target)
    )

    result
  end

  @spec record(atom(), keyword()) :: :ok
  def record(event, attrs \\ []) when is_atom(event) and is_list(attrs) do
    ids =
      attrs
      |> Keyword.take([:actor_id, :target_id])
      |> Enum.flat_map(fn {key, value} ->
        case Ecto.UUID.cast(value) do
          {:ok, uuid} -> [{key, uuid}]
          :error -> []
        end
      end)
      |> Enum.map_join(" ", fn {key, value} -> "#{key}=#{value}" end)

    suffix = if ids == "", do: "", else: " " <> ids
    Logger.info("security_event=#{event}" <> suffix)
    :ok
  end

  defp successful?(:ok), do: true
  defp successful?({:ok, _value}), do: true
  defp successful?(_result), do: false

  defp record_id(%User{id: id}) when is_binary(id), do: id
  defp record_id(%{id: id}) when is_binary(id), do: id
  defp record_id(_record), do: nil
end
