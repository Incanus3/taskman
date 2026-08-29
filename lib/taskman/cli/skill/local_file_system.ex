defmodule Taskman.CLI.Skill.LocalFileSystem do
  @moduledoc "Production filesystem adapter for skill installation."

  @behaviour Taskman.CLI.Skill.FileSystem

  @impl true
  def mkdir_p(path), do: File.mkdir_p(path)

  @impl true
  def write(path, contents), do: File.write(path, contents)

  @impl true
  def read(path), do: File.read(path)

  @impl true
  def exists?(path), do: File.exists?(path)

  @impl true
  def list(path), do: File.ls(path)

  @impl true
  def rename(from, to), do: File.rename(from, to)

  @impl true
  def rm_rf(path), do: File.rm_rf(path)
end
