defmodule Taskman.CLI.Skill.Bundle do
  @moduledoc "Compile-time bundle of the version-matched Taskman CLI skill."

  @skill_path Path.expand("../../../../priv/taskman_cli_skill/SKILL.md", __DIR__)
  @external_resource @skill_path
  @skill File.read!(@skill_path)

  @doc "Return the files written by the skill installer."
  @spec files() :: %{required(String.t()) => String.t()}
  def files, do: %{"SKILL.md" => @skill}

  @doc "Return the CLI version associated with the bundled skill."
  @spec cli_version() :: String.t()
  def cli_version, do: Taskman.CLI.version()
end
