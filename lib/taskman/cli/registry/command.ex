defmodule Taskman.CLI.Registry.Command do
  @moduledoc "Declarative metadata for one Taskman CLI command."

  alias Taskman.CLI.Registry.{Argument, Option}

  @type t :: %__MODULE__{
          path: [String.t()],
          summary: String.t(),
          usage: String.t(),
          handler: term(),
          arguments: [Argument.t()],
          options: [Option.t()],
          constraints: [term()],
          examples: [String.t()]
        }

  defstruct [
    :path,
    :summary,
    :usage,
    :handler,
    arguments: [],
    options: [],
    constraints: [],
    examples: []
  ]
end
