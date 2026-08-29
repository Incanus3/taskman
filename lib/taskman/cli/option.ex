defmodule Taskman.CLI.Option do
  @moduledoc "Declarative metadata for a command or global option."

  @type t :: %__MODULE__{
          name: atom(),
          long: String.t(),
          type: atom(),
          value_name: String.t() | nil,
          help: String.t(),
          values: [String.t()],
          required?: boolean()
        }

  defstruct [:name, :long, :type, :value_name, :help, values: [], required?: false]
end
