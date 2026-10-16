defmodule Taskman.CLI.Commands.Config do
  @moduledoc "Offline handlers for protected named-host configuration."
  alias Taskman.CLI.Config
  alias Taskman.CLI.Execution.{Invocation, Result}
  @scope_message "All repositories bound to this host use the updated URL. API key retained."

  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    runtime_options = Map.new(runtime_options)
    name = Map.get(invocation.arguments, :host)
    json? = Map.get(invocation.globals, :json, false)

    result =
      case action do
        :host_add ->
          save(
            Config.add_host(name, invocation.options.url, runtime_options),
            %{saved: true},
            "Saved Taskman host."
          )

        :host_set_url ->
          save(
            Config.set_host_url(name, invocation.options.url, runtime_options),
            %{saved: true, message: @scope_message},
            @scope_message
          )

        :host_remove ->
          save(
            Config.remove_host(name, runtime_options),
            %{removed: true},
            "Removed Taskman host."
          )

        :default_host ->
          save(
            Config.set_default_host(name, runtime_options),
            %{saved: true},
            "Saved default Taskman host."
          )

        :host_set_key ->
          set_key(name, runtime_options)

        :host_list ->
          with {:ok, document} <- Config.load(runtime_options) do
            hosts =
              document.hosts
              |> Enum.sort_by(&elem(&1, 0))
              |> Enum.map(fn {name, host} -> descriptor(name, host, document.default_host) end)

            {:ok, hosts, Enum.map_join(hosts, "\n", &host_text/1)}
          end

        :host_show ->
          with {:ok, document} <- Config.load(runtime_options),
               {:ok, host} <- fetch_host(document, name) do
            descriptor = descriptor(name, host, document.default_host)
            {:ok, descriptor, host_text(descriptor)}
          end

        :show ->
          context = Map.fetch!(runtime_options, :resolved_context)

          data = %{
            config_format: context.config_format,
            default_host: context.default_host,
            host: context.host,
            api_url: context.api_url,
            api_key_configured: context.api_key != nil,
            project_id: context.project_id,
            sources: context.sources
          }

          {:ok, data,
           "Host: #{context.host || "none"}\nAPI URL: #{context.api_url || "none"}\nAPI key: #{key_text(data.api_key_configured)}\nProject: #{context.project_id || "none"}"}
      end

    Result.from_local(result, json?)
  end

  defp descriptor(name, host, default_host),
    do: %{
      name: name,
      api_url: host.api_url,
      api_key_configured: host.api_key != nil,
      default: default_host == name
    }

  defp host_text(host),
    do:
      "#{host.name}\t#{host.api_url}\t#{key_text(host.api_key_configured)}#{if host.default, do: "\tdefault", else: ""}"

  defp key_text(true), do: "configured"
  defp key_text(false), do: "not configured"

  defp fetch_host(document, name) do
    case Map.fetch(document.hosts, name) do
      {:ok, host} -> {:ok, host}
      :error -> {:error, :invalid_invocation, "The named Taskman host is not registered"}
    end
  end

  defp set_key(name, options) do
    with {:ok, document} <- Config.load(options),
         {:ok, _host} <- fetch_host(document, name),
         {:ok, key} <- prompt(options) do
      save(Config.set_host_key(name, key, options), %{saved: true}, "Saved Taskman API key.")
    end
  end

  defp prompt(options) do
    terminal = Map.get(options, :terminal, Taskman.LocalTerminal)

    case terminal.prompt_secret("Taskman API key: ") do
      key when is_binary(key) -> {:ok, key}
      _ -> {:error, :input_unavailable, "Unable to read the Taskman API key from the terminal"}
    end
  rescue
    _ -> {:error, :input_unavailable, "Unable to read the Taskman API key from the terminal"}
  end

  defp save(:ok, data, text), do: {:ok, data, text}
  defp save(error, _data, _text), do: error
end
