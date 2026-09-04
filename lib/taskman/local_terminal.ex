defmodule Taskman.LocalTerminal do
  @behaviour Taskman.Terminal

  @impl Taskman.Terminal
  def prompt(message), do: prompt(message, :standard_io)

  def prompt(message, io_device) do
    io_device
    |> read_visible(message)
    |> normalize_visible_input()
  end

  @impl Taskman.Terminal
  def prompt_secret(message) do
    case start_shell(:raw) do
      :ok ->
        try do
          prompt_secret(message, :user)
        after
          start_shell(:cooked)
        end

      _ ->
        terminal_error()
    end
  end

  def prompt_secret(message, io_device) do
    io_device
    |> read_secret(message)
    |> normalize_secret_input()
  end

  defp read_visible(io_device, message) do
    safe_io_request(fn ->
      :io.get_line(io_device, String.to_charlist(message))
    end)
  end

  defp read_secret(io_device, message) do
    safe_io_request(fn ->
      case :io.put_chars(io_device, String.to_charlist(message)) do
        :ok -> :io.get_password(io_device)
        result -> result
      end
    end)
  end

  defp safe_io_request(request) do
    request.()
  rescue
    _error -> terminal_error()
  catch
    :exit, _reason -> terminal_error()
  end

  defp start_shell(mode) do
    :shell.start_interactive({:noshell, mode})
  rescue
    _error -> terminal_error()
  catch
    :exit, _reason -> terminal_error()
  end

  defp normalize_visible_input(input) when is_binary(input), do: String.trim(input)

  defp normalize_visible_input(input) when is_list(input) do
    input
    |> List.to_string()
    |> String.trim()
  end

  defp normalize_visible_input(:eof), do: terminal_error()
  defp normalize_visible_input({:error, _reason}), do: terminal_error()

  defp normalize_secret_input(input) when is_binary(input), do: trim_line_ending(input)

  defp normalize_secret_input(input) when is_list(input) do
    input
    |> List.to_string()
    |> trim_line_ending()
  end

  defp normalize_secret_input(:eof), do: terminal_error()
  defp normalize_secret_input({:error, _reason}), do: terminal_error()

  defp trim_line_ending(input) do
    input
    |> String.trim_trailing("\n")
    |> String.trim_trailing("\r")
  end

  defp terminal_error, do: {:error, :input_unavailable}
end
