defmodule Taskman.CLI.Context do
  @moduledoc "The selected named host, Project scope and associated credential for one invocation."

  alias Taskman.CLI.{Config, RepositoryBinding}
  alias Taskman.CLI.Config.Document
  alias Taskman.CLI.Execution.Invocation

  @type t :: %__MODULE__{
          host: String.t() | nil,
          api_url: String.t() | nil,
          api_key: String.t() | nil,
          project_id: pos_integer() | nil,
          config_format: :absent | :legacy | :v2,
          default_host: String.t() | nil,
          common_git_dir: String.t() | nil,
          sources: %{host: atom(), project: atom(), key: atom()}
        }
  @type local_error ::
          {:error, :invalid_invocation | :invalid_configuration | :authentication_required,
           String.t()}

  defstruct host: nil,
            api_url: nil,
            api_key: nil,
            project_id: nil,
            config_format: :absent,
            default_host: nil,
            common_git_dir: nil,
            sources: %{host: :none, project: :none, key: :none}

  @doc "Resolve one invocation without HTTP, writes, or lower-level target fallback."
  @spec resolve(Invocation.t(), keyword() | map()) :: {:ok, t()} | local_error()
  def resolve(invocation, options \\ [])

  def resolve(%Invocation{command: %{context_role: :local}}, _options), do: {:ok, %__MODULE__{}}

  def resolve(%Invocation{} = invocation, options) do
    role = invocation.command.context_role
    options = Map.new(options)
    env = Map.new(Map.get(options, :env, System.get_env()))
    options = options |> Map.put(:env, env) |> Map.put_new_lazy(:cwd, &File.cwd!/0)

    with :ok <- validate_explicit(invocation, role),
         :ok <- reject_retired_url(env),
         {:ok, document} <- Config.load(options),
         {:ok, binding, common_dir} <- repository_context(invocation, role, options),
         :ok <- validate_paired_scope(invocation, role, binding),
         {:ok, host, host_source} <- select_host(invocation, binding, document, env),
         {:ok, project_id, project_source} <- select_project(invocation, role, binding),
         {:ok, host_entry} <- fetch_host(document, host, role),
         {:ok, api_key, key_source} <- select_key(host, host_entry, env, role) do
      {:ok,
       %__MODULE__{
         host: host,
         api_url: host_entry.api_url,
         api_key: api_key,
         project_id: project_id,
         config_format: document.format,
         default_host: document.default_host,
         common_git_dir: common_dir,
         sources: %{host: host_source, project: project_source, key: key_source}
       }}
    end
  end

  defp validate_explicit(invocation, role) do
    host? = Map.has_key?(invocation.globals, :host)
    project? = Map.has_key?(invocation.options, :project)
    scoped? = role in [:project_required, :project_filter, :bind]

    cond do
      host? and Document.validate_host_name(invocation.globals.host) != :ok ->
        invocation_error("Supply a valid lowercase host name with --host")

      scoped? and project? and
          not (is_integer(invocation.options.project) and invocation.options.project > 0) ->
        invocation_error("Supply a positive integer Project ID with --project")

      role == :project_filter and project? and all_projects?(invocation) ->
        invocation_error("--project and --all-projects cannot be used together")

      role == :bind and not (host? and project?) ->
        invocation_error("Repository binding requires both --host and --project")

      role == :project_required and host? and not project? ->
        invocation_error("Supply both --host and --project to select an explicit Project context")

      true ->
        :ok
    end
  end

  defp reject_retired_url(env) do
    if present(env, "TASKMAN_API_URL") == nil,
      do: :ok,
      else:
        configuration_error(
          "TASKMAN_API_URL is no longer supported; register a named host and select it with --host or TASKMAN_HOST"
        )
  end

  defp repository_context(invocation, role, options) do
    if complete_domain_target?(invocation, role) do
      {:ok, :unbound, nil}
    else
      case RepositoryBinding.discover(options) do
        {:ok, :outside_work_tree} when role == :bind ->
          invocation_error("Repository binding requires a Git working tree")

        {:ok, :outside_work_tree} ->
          {:ok, :unbound, nil}

        {:ok, common_dir} ->
          with {:ok, binding} <- RepositoryBinding.read(common_dir, options) do
            {:ok, binding, common_dir}
          end

        error ->
          error
      end
    end
  end

  defp complete_domain_target?(invocation, role) do
    host? = Map.has_key?(invocation.globals, :host)
    project? = Map.has_key?(invocation.options, :project)

    host? and
      (role == :host_only or
         (role in [:project_required, :project_filter] and project?) or
         (role == :project_filter and all_projects?(invocation)))
  end

  defp validate_paired_scope(_invocation, _role, :unbound), do: :ok

  defp validate_paired_scope(invocation, role, _binding)
       when role in [:project_required, :project_filter] do
    host? = Map.has_key?(invocation.globals, :host)
    project? = Map.has_key?(invocation.options, :project)

    if host? != project? and not (role == :project_filter and all_projects?(invocation)),
      do:
        invocation_error(
          "Supply both --host and --project to override repository Project context"
        ),
      else: :ok
  end

  defp validate_paired_scope(_invocation, _role, _binding), do: :ok

  defp select_host(invocation, binding, document, env) do
    cond do
      Map.has_key?(invocation.globals, :host) ->
        {:ok, invocation.globals.host, :flag}

      binding != :unbound ->
        {:ok, binding.host, :repository}

      present(env, "TASKMAN_HOST") != nil ->
        with :ok <- Document.validate_host_name(env["TASKMAN_HOST"]) do
          {:ok, env["TASKMAN_HOST"], :environment}
        end

      document.default_host != nil ->
        {:ok, document.default_host, :default}

      true ->
        {:ok, nil, :none}
    end
  end

  defp select_project(invocation, role, binding) do
    cond do
      role in [:host_only, :local] ->
        {:ok, nil, :none}

      role == :effective_inspection and Map.has_key?(invocation.globals, :host) ->
        {:ok, nil, :none}

      role == :project_filter and all_projects?(invocation) ->
        {:ok, nil, :none}

      role in [:project_required, :project_filter, :bind] and
          Map.has_key?(invocation.options, :project) ->
        {:ok, invocation.options.project, :flag}

      binding != :unbound ->
        {:ok, binding.project_id, :repository}

      role == :project_required ->
        invocation_error("Supply --project or bind this repository to a Project")

      true ->
        {:ok, nil, :none}
    end
  end

  defp fetch_host(_document, nil, :effective_inspection), do: {:ok, %{api_url: nil, api_key: nil}}

  defp fetch_host(_document, nil, _role),
    do: invocation_error("Select a host with --host or TASKMAN_HOST, or configure a default host")

  defp fetch_host(document, host, _role) do
    case Map.fetch(document.hosts, host) do
      {:ok, entry} ->
        {:ok, entry}

      :error ->
        configuration_error(
          "The selected host is not registered; inspect config hosts or register it"
        )
    end
  end

  defp select_key(host, entry, env, role) do
    key = present(env, "TASKMAN_API_KEY")
    key_host = present(env, "TASKMAN_API_KEY_HOST")

    with :ok <- validate_injected_pair(key, key_host, host) do
      {selected_key, source} =
        cond do
          host == nil -> {nil, :none}
          key != nil -> {key, :environment}
          entry.api_key != nil -> {entry.api_key, :stored}
          true -> {nil, :none}
        end

      if selected_key == nil and role != :effective_inspection,
        do:
          {:error, :authentication_required,
           "Configure an API key for the selected host or inject a matching credential pair"},
        else: {:ok, selected_key, source}
    end
  end

  defp validate_injected_pair(nil, nil, _host), do: :ok

  defp validate_injected_pair(key, key_host, host) do
    cond do
      key == nil or key_host == nil ->
        configuration_error("Supply TASKMAN_API_KEY and TASKMAN_API_KEY_HOST together")

      Document.validate_api_key(key) != :ok or Document.validate_host_name(key_host) != :ok ->
        configuration_error(
          "The injected credential pair must contain a valid API key and host name"
        )

      host != nil and key_host != host ->
        configuration_error("The injected credential host must match the selected host name")

      true ->
        :ok
    end
  end

  defp present(env, key) do
    case Map.get(env, key) do
      value when value in [nil, ""] -> nil
      value -> value
    end
  end

  defp all_projects?(invocation), do: Map.get(invocation.options, :all_projects, false) == true
  defp invocation_error(message), do: {:error, :invalid_invocation, message}
  defp configuration_error(message), do: {:error, :invalid_configuration, message}
end
