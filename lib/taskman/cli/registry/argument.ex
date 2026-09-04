defmodule Taskman.CLI.Registry.Argument do
  @moduledoc "Declarative metadata for a positional command argument."

  @type t :: %__MODULE__{
          name: atom(),
          value_name: String.t(),
          type: atom(),
          help: String.t()
        }

  defstruct [:name, :value_name, :type, :help]
end
