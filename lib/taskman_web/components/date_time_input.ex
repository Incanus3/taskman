defmodule TaskmanWeb.DateTimeInput do
  @moduledoc "Presentation and web-form normalization for local date/time values."

  @localized ~r/\A(\d{2})\.(\d{2})\.(\d{4}) (\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)\z/
  @iso ~r/\A(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)\z/

  def normalize_params(params, field_key) do
    case Map.fetch(params, field_key) do
      {:ok, value} -> Map.put(params, field_key, canonical(value))
      :error -> params
    end
  end

  def canonical(nil), do: ""

  def canonical(%NaiveDateTime{second: 0, microsecond: {0, _}} = value),
    do: Calendar.strftime(value, "%Y-%m-%dT%H:%M")

  def canonical(%NaiveDateTime{} = value), do: NaiveDateTime.to_iso8601(value)

  def canonical(value) when is_binary(value) do
    trimmed = String.trim(value)

    candidate =
      case Regex.run(@localized, trimmed) do
        [_, day, month, year, time] -> "#{year}-#{month}-#{day}T#{time}"
        _ -> trimmed
      end

    case Regex.run(@iso, candidate) do
      [_, year, month, day, time] ->
        iso = "#{year}-#{month}-#{day}T#{time}"
        if valid?(iso), do: iso, else: value

      _ ->
        if trimmed == "", do: "", else: value
    end
  end

  def format(value) do
    value = canonical(value)

    case Regex.run(@iso, value) do
      [_, year, month, day, time] ->
        if valid?(value), do: "#{day}.#{month}.#{year} #{minute_or_seconds(time)}", else: value

      _ ->
        value
    end
  end

  defp minute_or_seconds(<<minute::binary-size(5), ":00">>), do: minute
  defp minute_or_seconds(time), do: time
  defp valid?(value), do: match?({:ok, _}, Ecto.Type.cast(:naive_datetime, value))
end
