defmodule Taskman.CLI.Skill.FileSystem do
  @moduledoc "Filesystem boundary used by the safe skill installer."

  @callback mkdir_p(Path.t()) :: :ok | {:error, term()}
  @callback mkdir_exclusive(Path.t()) :: :ok | {:error, term()}
  @callback write(Path.t(), iodata()) :: :ok | {:error, term()}
  @callback write_exclusive(Path.t(), iodata()) :: :ok | {:error, term()}
  @callback read(Path.t()) :: {:ok, binary()} | {:error, term()}
  @callback lstat(Path.t()) :: {:ok, File.Stat.t()} | {:error, term()}
  @callback exists?(Path.t()) :: boolean()
  @callback list(Path.t()) :: {:ok, [String.t()]} | {:error, term()}
  @callback rename(Path.t(), Path.t()) :: :ok | {:error, term()}
  @callback rm_rf(Path.t()) :: {:ok, [Path.t()]} | {:error, term()}
end
