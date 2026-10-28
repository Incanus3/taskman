defmodule TaskmanWeb.DateTimeInputTest do
  use ExUnit.Case, async: true
  alias TaskmanWeb.DateTimeInput

  test "normalizes only valid day-first local values and preserves other form fields" do
    for {display, iso} <- [
          {"28.10.2026 17:45", "2026-10-28T17:45"},
          {"29.02.2028 00:00", "2028-02-29T00:00"},
          {"06.05.0999 09:30", "0999-05-06T09:30"},
          {"06.05.0000 09:30", "0000-05-06T09:30"},
          {"06.05.1960 09:30:42.123456", "1960-05-06T09:30:42.123456"}
        ] do
      assert DateTimeInput.normalize_params(
               %{"starts_at" => display, "title" => "Keep"},
               "starts_at"
             ) ==
               %{"starts_at" => iso, "title" => "Keep"}

      assert DateTimeInput.format(iso) == display
    end

    assert DateTimeInput.normalize_params(%{"starts_at" => " 28.10.2026 17:45 "}, "starts_at") ==
             %{
               "starts_at" => "2026-10-28T17:45"
             }

    assert DateTimeInput.normalize_params(%{"starts_at" => " \t\n "}, "starts_at") == %{
             "starts_at" => ""
           }

    assert DateTimeInput.normalize_params(%{"title" => "Keep"}, "starts_at") == %{
             "title" => "Keep"
           }
  end

  test "normalizes only the explicit key, including atom keys" do
    params = %{"starts_at" => "28.10.2026 17:45", "due_at" => "28.10.2026 18:45"}

    assert DateTimeInput.normalize_params(params, "starts_at") ==
             %{"starts_at" => "2026-10-28T17:45", "due_at" => "28.10.2026 18:45"}

    assert DateTimeInput.normalize_params(%{starts_at: "28.10.2026 17:45"}, :starts_at) ==
             %{starts_at: "2026-10-28T17:45"}
  end

  test "invalid or incomplete input is retained exactly rather than clearing or reusing a value" do
    for raw <- [
          "31.02.2026 17:45",
          "29.02.2027 00:00",
          "28.10.2026 24:00",
          "28.10.2026 17:60",
          "28.10.",
          "unfinished",
          "",
          "2026-02-31T17:45"
        ] do
      assert DateTimeInput.normalize_params(%{"starts_at" => raw}, "starts_at") == %{
               "starts_at" => raw
             }

      assert DateTimeInput.format(raw) == raw
    end
  end

  test "formats persisted values and keeps ISO form compatibility and existing seconds" do
    assert DateTimeInput.format(nil) == ""
    assert DateTimeInput.format(~N[2026-10-28 17:45:00]) == "28.10.2026 17:45"
    assert DateTimeInput.format(~N[1960-05-06 09:30:42]) == "06.05.1960 09:30:42"

    assert DateTimeInput.normalize_params(%{"starts_at" => "2026-10-28T17:45"}, "starts_at") == %{
             "starts_at" => "2026-10-28T17:45"
           }
  end

  test "ISO compatibility uses one canonical separator for calendar synchronization" do
    assert DateTimeInput.canonical("2026-10-28 17:45") == "2026-10-28T17:45"
    assert DateTimeInput.format("2026-10-28 17:45") == "28.10.2026 17:45"
    assert DateTimeInput.canonical("2026-02-31 17:45") == "2026-02-31 17:45"
  end
end
