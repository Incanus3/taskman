defmodule Taskman.CLI.Commands.Config do
  @moduledoc "Offline handlers for protected Taskman CLI configuration."

  alias Taskman.CLI.{Config, Invocation, Output, Result}

  @doc "Execute a configuration command without contacting the Taskman API."
  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    json? = Map.get(invocation.globals, :json, false)

    case action do
      :set_url ->
        invocation.arguments
        |> Map.fetch!(:api_url)
        |> Config.set_url(runtime_options)
        |> render_save("Saved Taskman API URL.\n", json?)

      :set_key ->
        set_key(runtime_options, json?)

      :show ->
        case Config.resolve(invocation.globals, runtime_options) do
          {:ok, resolved} -> render_show(Config.display(resolved), json?)
          {:error, :invalid_configuration, message} -> invalid_configuration(message, json?)
        end

      _other ->
        internal_error(json?)
    end
  end

  defp set_key(runtime_options, json?) do
    terminal = runtime_option(runtime_options, :terminal, Taskman.LocalTerminal)

    case terminal.prompt_secret("Taskman API key: ") do
      api_key when is_binary(api_key) ->
        api_key
        |> Config.set_key(runtime_options)
        |> render_save("Saved Taskman API key.\n", json?)

      {:error, :input_unavailable} ->
        input_unavailable(json?)

      _other ->
        input_unavailable(json?)
    end
  rescue
    _error -> input_unavailable(json?)
  end

  defp render_save(:ok, _readable, true),
    do: %Result{stdout: Jason.encode!(%{data: %{saved: true}}) <> "\n"}

  defp render_save(:ok, readable, false), do: %Result{stdout: readable}

  defp render_save({:error, :invalid_configuration, message}, _readable, json?),
    do: invalid_configuration(message, json?)

  defp render_show(display, true), do: %Result{stdout: Jason.encode!(%{data: display}) <> "\n"}

  defp render_show(%{api_url: api_url, api_key_configured: configured?}, false) do
    key_status = if configured?, do: "configured", else: "not configured"
    %Result{stdout: "API URL: #{api_url}\nAPI key: #{key_status}\n"}
  end

  defp invalid_configuration(message, json?) do
    envelope = %{"error" => %{"code" => "invalid_configuration", "message" => message}}
    %Result{status: 2, stderr: Output.error(envelope, json?)}
  end

  defp input_unavailable(json?) do
    envelope = %{
      "error" => %{
        "code" => "input_unavailable",
        "message" => "Unable to read the Taskman API key from the terminal"
      }
    }

    %Result{status: 5, stderr: Output.error(envelope, json?)}
  end

  defp internal_error(json?) do
    envelope = %{
      "error" => %{"code" => "internal_error", "message" => "Unsupported config command"}
    }

    %Result{status: 5, stderr: Output.error(envelope, json?)}
  end

  defp runtime_option(options, key, default) when is_list(options),
    do: Keyword.get(options, key, default)

  defp runtime_option(options, key, default) when is_map(options),
    do: Map.get(options, key, default)

  defp runtime_option(_options, _key, default), do: default
end
