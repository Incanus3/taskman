defmodule Taskman.CLI.Skill.FakeSkillFileSystem do
  @moduledoc false

  def fail_next_rename!(kind \\ :stage_to_target) do
    fail_rename_sequence!([kind])
  end

  def fail_rename_sequence!(kinds) when is_list(kinds) do
    Process.put({__MODULE__, :rename_failures}, kinds)
    :ok
  end

  def fail_next_write! do
    Process.put({__MODULE__, :write_failure}, true)
    :ok
  end

  def fail_next_rm_rf! do
    Process.put({__MODULE__, :rm_rf_failure}, true)
    :ok
  end

  def reset! do
    Process.delete({__MODULE__, :rename_failures})
    Process.delete({__MODULE__, :write_failure})
    Process.delete({__MODULE__, :rm_rf_failure})
    :ok
  end

  def mkdir_p(path), do: File.mkdir_p(path)

  def write(path, contents) do
    if Process.get({__MODULE__, :write_failure}) do
      Process.delete({__MODULE__, :write_failure})
      {:error, :injected_write_failure}
    else
      File.write(path, contents)
    end
  end

  def read(path), do: File.read(path)
  def exists?(path), do: File.exists?(path)
  def list(path), do: File.ls(path)

  def rename(from, to) do
    failures = Process.get({__MODULE__, :rename_failures}, [])

    if rename_should_fail?(List.first(failures), from, to) do
      update_rename_failures(List.delete_at(failures, 0))
      {:error, :injected_rename_failure}
    else
      File.rename(from, to)
    end
  end

  def rm_rf(path) do
    if Process.get({__MODULE__, :rm_rf_failure}) do
      Process.delete({__MODULE__, :rm_rf_failure})
      {:error, :injected_rm_rf_failure}
    else
      File.rm_rf(path)
    end
  end

  defp rename_should_fail?(:stage_to_target, from, to) do
    Path.basename(to) == "taskman-cli" and
      String.starts_with?(Path.basename(from), ".taskman-cli.stage-")
  end

  defp rename_should_fail?(:restore, from, to) do
    Path.basename(to) == "taskman-cli" and
      String.starts_with?(Path.basename(from), ".taskman-cli.backup-")
  end

  defp rename_should_fail?(:any, _from, _to), do: true
  defp rename_should_fail?(_failure, _from, _to), do: false

  defp update_rename_failures([]), do: Process.delete({__MODULE__, :rename_failures})
  defp update_rename_failures(failures), do: Process.put({__MODULE__, :rename_failures}, failures)
end
