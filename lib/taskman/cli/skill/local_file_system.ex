defmodule Taskman.CLI.Skill.LocalFileSystem do
  @moduledoc "Production filesystem adapter for skill installation."

  @behaviour Taskman.CLI.Skill.FileSystem

  @impl true
  def mkdir_p(path), do: File.mkdir_p(path)

  @impl true
  def mkdir_exclusive(path), do: File.mkdir(path)

  @impl true
  def write(path, contents), do: File.write(path, contents)

  @impl true
  def write_exclusive(path, contents) do
    case File.open(path, [:write, :exclusive, :binary], fn device ->
           IO.binwrite(device, contents)
         end) do
      {:ok, :ok} -> :ok
      {:error, _reason} = error -> error
    end
  end

  @impl true
  def read(path), do: File.read(path)

  @impl true
  def lstat(path), do: File.lstat(path)

  @impl true
  def exists?(path), do: File.exists?(path)

  @impl true
  def list(path), do: File.ls(path)

  @impl true
  def rename(from, to), do: File.rename(from, to)

  @impl true
  def rm_rf(path), do: File.rm_rf(path)
end
