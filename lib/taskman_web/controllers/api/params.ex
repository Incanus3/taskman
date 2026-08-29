defmodule TaskmanWeb.API.Params do
  @moduledoc """
  Helpers for validating primitive values received at the JSON API boundary.
  """

  @spec positive_id(term()) :: {:ok, pos_integer()} | {:error, :invalid_request}
  def positive_id(value) when is_integer(value) and value > 0, do: {:ok, value}

  def positive_id(value) when is_binary(value) do
    case Integer.parse(value) do
      {id, ""} when id > 0 -> {:ok, id}
      _invalid -> {:error, :invalid_request}
    end
  end

  def positive_id(_value), do: {:error, :invalid_request}
end
