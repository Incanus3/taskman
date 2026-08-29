defmodule Taskman.CLI.Skill.Installer do
  @moduledoc "Install the bundled Taskman CLI skill with atomic replacement and rollback."

  alias Taskman.CLI.Skill.{Bundle, LocalFileSystem}

  @skill_name "taskman-cli"
  @marker_name ".taskman-managed.json"
  @installer_name "taskman"
  @stage_sibling_prefix ".taskman-cli.stage-"
  @backup_sibling_prefix ".taskman-cli.backup-"
  @owned_sibling_pattern ~r/\A\.taskman-cli\.(?:stage|backup)-[0-9]+\z/

  @typedoc "The result returned after a successful skill installation."
  @type result :: %{
          action: :installed | :updated | :current,
          path: String.t(),
          skill: String.t(),
          cli_version: String.t()
        }

  @doc "Install or update the version-matched bundled skill."
  @spec install(keyword() | map()) ::
          {:ok, result()}
          | {:error, :skill_install_failed, String.t()}
  def install(options \\ []) do
    install(options, valid_options?(options))
  end

  defp install(options, true) do
    options = normalize_options(options)
    file_system = Keyword.get(options, :file_system, LocalFileSystem)
    force? = Keyword.get(options, :force, false) == true

    skills_root =
      case Keyword.fetch(options, :skills_root) do
        {:ok, root} -> root
        :error -> default_skills_root()
      end

    with {:ok, skills_root} <- validate_skills_root(skills_root),
         :ok <- mkdir_p(file_system, skills_root),
         {:ok, state} <- target_state(file_system, Path.join(skills_root, @skill_name)) do
      target = Path.join(skills_root, @skill_name)
      install_target_state(file_system, skills_root, target, state, force?)
    else
      {:error, message} -> failure(message)
    end
  end

  defp install(_options, false), do: failure("Installer options must be a keyword list or map")

  defp normalize_options(options) when is_list(options), do: options
  defp normalize_options(options) when is_map(options), do: Map.to_list(options)

  defp valid_options?(options), do: is_list(options) or is_map(options)

  defp default_skills_root do
    Path.join(System.user_home!(), ".agents/skills")
  end

  defp validate_skills_root(root) when is_binary(root) and byte_size(root) > 0, do: {:ok, root}
  defp validate_skills_root(_root), do: {:error, "Skill root must be a non-empty path"}

  defp target_state(file_system, target) do
    if fs_exists?(file_system, target) do
      case read_marker(file_system, target) do
        {:ok, marker} -> {:ok, {:recognized, marker}}
        :unrecognized -> {:ok, :unrecognized}
      end
    else
      {:ok, :missing}
    end
  end

  defp read_marker(file_system, target) do
    marker_path = Path.join(target, @marker_name)

    case fs_read(file_system, marker_path) do
      {:ok, contents} ->
        case Jason.decode(contents) do
          {:ok, marker} when is_map(marker) ->
            if recognized_marker?(marker), do: {:ok, marker}, else: :unrecognized

          _other ->
            :unrecognized
        end

      {:error, _reason} ->
        :unrecognized
    end
  end

  defp recognized_marker?(%{
         "installer" => @installer_name,
         "skill" => @skill_name,
         "cli_version" => version
       })
       when is_binary(version),
       do: true

  defp recognized_marker?(_marker), do: false

  defp target_current?(file_system, target, marker) do
    marker == marker() and bundle_matches?(file_system, target)
  end

  defp bundle_matches?(file_system, target) do
    Enum.all?(Bundle.files(), fn {relative_path, contents} ->
      fs_read(file_system, Path.join(target, relative_path)) == {:ok, contents}
    end)
  end

  defp complete_skill?(file_system, target) do
    Enum.all?(Bundle.files(), fn {relative_path, _contents} ->
      match?({:ok, _contents}, fs_read(file_system, Path.join(target, relative_path)))
    end)
  end

  defp install_target_state(file_system, skills_root, target, :missing, force?) do
    case reconcile_missing_target(file_system, skills_root, target) do
      :install -> replace(file_system, skills_root, target, :installed, false)
      {:ok, state} -> install_target_state(file_system, skills_root, target, state, force?)
      {:error, message} -> failure(message)
    end
  end

  defp install_target_state(file_system, skills_root, target, {:recognized, marker}, _force?) do
    if target_current?(file_system, target, marker) do
      finalize_success(file_system, skills_root, :current, target)
    else
      replace(file_system, skills_root, target, :updated, true)
    end
  end

  defp install_target_state(file_system, skills_root, target, :unrecognized, true) do
    replace(file_system, skills_root, target, :updated, true)
  end

  defp install_target_state(_file_system, _skills_root, target, :unrecognized, false) do
    failure(
      "Refusing to replace unrecognized skill directory at #{target}; pass --force to replace it"
    )
  end

  defp reconcile_missing_target(file_system, skills_root, target) do
    with {:ok, %{stages: stages, backups: backups}} <-
           owned_sibling_groups(file_system, skills_root) do
      cond do
        length(stages) > 1 ->
          {:error,
           "Refusing to recover missing skill target at #{target}: multiple installer staging siblings remain"}

        length(backups) > 1 ->
          {:error,
           "Refusing to recover missing skill target at #{target}: multiple installer backups remain"}

        backups != [] and stages != [] ->
          {:error,
           "Refusing to recover missing skill target at #{target}: installer backup and staging siblings remain"}

        backups == [] ->
          :install

        true ->
          [backup] = backups
          recover_backup(file_system, backup, target)
      end
    else
      {:error, message} -> {:error, "Could not inspect recovery paths: #{message}"}
    end
  end

  defp recover_backup(file_system, backup, target) do
    case target_state(file_system, backup) do
      {:ok, {:recognized, _marker}} ->
        if complete_skill?(file_system, backup) do
          case restore_backup(file_system, backup, target) do
            :ok ->
              case target_state(file_system, target) do
                {:ok, :missing} ->
                  {:error, "Recovered backup #{backup}, but the skill target remains absent"}

                state ->
                  state
              end

            {:error, {first_reason, retry_reason}} ->
              {:error,
               "Could not recover previous skill from #{backup} after retry: #{inspect(first_reason)}; retry failed: #{inspect(retry_reason)}"}
          end
        else
          {:error, "Refusing to recover incomplete installer backup at #{backup}"}
        end

      _other ->
        {:error, "Refusing to recover unrecognized installer backup at #{backup}"}
    end
  end

  defp replace(file_system, skills_root, target, action, update?) do
    stage = unique_sibling(file_system, skills_root, "stage")
    backup = if update?, do: unique_sibling(file_system, skills_root, "backup"), else: nil

    case stage_skill(file_system, stage) do
      :ok -> swap(file_system, skills_root, stage, backup, target, action)
      {:error, message} -> failure_after_cleanup(file_system, stage, message)
    end
  end

  defp stage_skill(file_system, stage) do
    with :ok <- mkdir_p(file_system, stage),
         :ok <- write_files(file_system, stage, Bundle.files()),
         :ok <- write_marker(file_system, stage) do
      :ok
    end
  end

  defp write_files(file_system, stage, files) do
    Enum.reduce_while(files, :ok, fn {relative_path, contents}, :ok ->
      path = Path.join(stage, relative_path)

      case fs_write(file_system, path, contents) do
        :ok ->
          {:cont, :ok}

        {:error, reason} ->
          {:halt, {:error, "Could not stage #{relative_path}: #{inspect(reason)}"}}
      end
    end)
  end

  defp write_marker(file_system, stage) do
    contents = Jason.encode!(marker()) <> "\n"

    case fs_write(file_system, Path.join(stage, @marker_name), contents) do
      :ok -> :ok
      {:error, reason} -> {:error, "Could not stage #{@marker_name}: #{inspect(reason)}"}
    end
  end

  defp swap(file_system, skills_root, stage, nil, target, action) do
    case fs_rename(file_system, stage, target) do
      :ok ->
        case ensure_removed(file_system, stage) do
          :ok -> finalize_success(file_system, skills_root, action, target)
          {:error, message} -> failure(message)
        end

      {:error, reason} ->
        failure_after_cleanup(
          file_system,
          stage,
          "Could not install staged skill at #{target}: #{inspect(reason)}"
        )
    end
  end

  defp swap(file_system, skills_root, stage, backup, target, action) do
    case fs_rename(file_system, target, backup) do
      :ok ->
        case fs_rename(file_system, stage, target) do
          :ok ->
            with :ok <- ensure_removed(file_system, backup),
                 :ok <- ensure_removed(file_system, stage) do
              finalize_success(file_system, skills_root, action, target)
            else
              {:error, message} -> failure(message)
            end

          {:error, swap_reason} ->
            restore_after_failed_swap(file_system, stage, backup, target, swap_reason)
        end

      {:error, reason} ->
        failure_after_cleanup(
          file_system,
          stage,
          "Could not move existing skill at #{target} to backup: #{inspect(reason)}"
        )
    end
  end

  defp restore_after_failed_swap(file_system, stage, backup, target, swap_reason) do
    restore_result = restore_backup(file_system, backup, target)
    cleanup_result = remove_for_failure(file_system, stage)

    message =
      case restore_result do
        :ok ->
          "Could not install staged skill at #{target}: #{inspect(swap_reason)}; previous skill restored"

        {:error, {first_reason, retry_reason}} ->
          "Could not install staged skill at #{target}: #{inspect(swap_reason)}; restoring previous skill failed after retry: #{inspect(first_reason)}; retry failed: #{inspect(retry_reason)}"
      end

    case cleanup_result do
      :ok -> failure(message)
      {:error, cleanup_message} -> failure("#{message}; #{cleanup_message}")
    end
  end

  defp restore_backup(file_system, backup, target) do
    case fs_rename(file_system, backup, target) do
      :ok ->
        :ok

      {:error, first_reason} ->
        case fs_rename(file_system, backup, target) do
          :ok -> :ok
          {:error, retry_reason} -> {:error, {first_reason, retry_reason}}
        end
    end
  end

  defp finalize_success(file_system, skills_root, action, target) do
    case cleanup_owned_siblings(file_system, skills_root) do
      :ok -> {:ok, success(action, target)}
      {:error, message} -> failure(message)
    end
  end

  defp cleanup_owned_siblings(file_system, skills_root) do
    with {:ok, paths} <- owned_sibling_paths(file_system, skills_root),
         :ok <- remove_owned_siblings(file_system, paths),
         {:ok, remaining} <- owned_sibling_paths(file_system, skills_root) do
      case remaining do
        [] ->
          :ok

        _paths ->
          {:error,
           "temporary cleanup failed: installer-owned temporary paths remain under #{skills_root}"}
      end
    else
      {:error, message} -> {:error, "temporary cleanup failed: #{message}"}
    end
  end

  defp owned_sibling_paths(file_system, skills_root) do
    case owned_sibling_groups(file_system, skills_root) do
      {:ok, %{backups: backups}} ->
        paths =
          Enum.filter(backups, fn path ->
            match?({:ok, {:recognized, _marker}}, target_state(file_system, path))
          end)

        {:ok, paths}

      {:error, message} ->
        {:error, message}
    end
  end

  defp owned_sibling_groups(file_system, skills_root) do
    case fs_list(file_system, skills_root) do
      {:ok, names} when is_list(names) ->
        groups =
          Enum.reduce(names, %{stages: [], backups: []}, fn name, groups ->
            case owned_sibling_kind(name) do
              :stage -> Map.update!(groups, :stages, &[Path.join(skills_root, name) | &1])
              :backup -> Map.update!(groups, :backups, &[Path.join(skills_root, name) | &1])
              nil -> groups
            end
          end)

        {:ok,
         %{
           stages: Enum.reverse(groups.stages),
           backups: Enum.reverse(groups.backups)
         }}

      {:ok, other} ->
        {:error, "Could not inspect temporary paths under #{skills_root}: #{inspect(other)}"}

      {:error, reason} ->
        {:error, "Could not inspect temporary paths under #{skills_root}: #{inspect(reason)}"}
    end
  end

  defp remove_owned_siblings(file_system, paths) do
    Enum.reduce_while(paths, :ok, fn path, :ok ->
      case ensure_removed(file_system, path) do
        :ok -> {:cont, :ok}
        {:error, message} -> {:halt, {:error, message}}
      end
    end)
  end

  defp owned_sibling_kind(name) when is_binary(name) do
    if Regex.match?(@owned_sibling_pattern, name) do
      cond do
        String.starts_with?(name, @stage_sibling_prefix) -> :stage
        String.starts_with?(name, @backup_sibling_prefix) -> :backup
      end
    end
  end

  defp owned_sibling_kind(_name), do: nil

  defp failure_after_cleanup(file_system, stage, message) do
    case remove_for_failure(file_system, stage) do
      :ok -> failure(message)
      {:error, cleanup_message} -> failure("#{message}; #{cleanup_message}")
    end
  end

  defp remove_for_failure(file_system, path) do
    case ensure_removed(file_system, path) do
      :ok -> :ok
      {:error, message} -> {:error, "staging cleanup failed: #{message}"}
    end
  end

  defp ensure_removed(file_system, path) do
    if fs_exists?(file_system, path) do
      case fs_rm_rf(file_system, path) do
        :ok ->
          if fs_exists?(file_system, path) do
            {:error, "temporary path remains at #{path}"}
          else
            :ok
          end

        {:error, reason} ->
          {:error, "Could not remove temporary path #{path}: #{inspect(reason)}"}
      end
    else
      :ok
    end
  end

  defp unique_sibling(file_system, skills_root, kind) do
    candidate =
      Path.join(
        skills_root,
        ".taskman-cli.#{kind}-#{System.unique_integer([:positive, :monotonic])}"
      )

    if fs_exists?(file_system, candidate) do
      unique_sibling(file_system, skills_root, kind)
    else
      candidate
    end
  end

  defp marker do
    %{
      "installer" => @installer_name,
      "skill" => @skill_name,
      "cli_version" => Bundle.cli_version()
    }
  end

  defp success(action, target) do
    %{action: action, path: target, skill: @skill_name, cli_version: Bundle.cli_version()}
  end

  defp failure(message), do: {:error, :skill_install_failed, message}

  defp mkdir_p(file_system, path) do
    case fs_call(file_system, :mkdir_p, [path]) do
      :ok -> :ok
      {:error, reason} -> {:error, "Could not create skill root #{path}: #{inspect(reason)}"}
      other -> {:error, "Could not create skill root #{path}: #{inspect(other)}"}
    end
  end

  defp fs_read(file_system, path), do: fs_call(file_system, :read, [path])
  defp fs_write(file_system, path, contents), do: fs_call(file_system, :write, [path, contents])
  defp fs_exists?(file_system, path), do: fs_call(file_system, :exists?, [path]) == true
  defp fs_list(file_system, path), do: fs_call(file_system, :list, [path])
  defp fs_rename(file_system, from, to), do: fs_call(file_system, :rename, [from, to])

  defp fs_rm_rf(file_system, path) do
    case fs_call(file_system, :rm_rf, [path]) do
      :ok -> :ok
      {:ok, _removed} -> :ok
      {:error, reason} -> {:error, reason}
      other -> {:error, other}
    end
  end

  defp fs_call(file_system, function, args) when is_atom(file_system) do
    apply(file_system, function, args)
  rescue
    error -> {:error, Exception.message(error)}
  end

  defp fs_call(_file_system, _function, _args), do: {:error, :invalid_file_system}
end
