defmodule Taskman.CLI.Result do
  @moduledoc "A value returned by the CLI before IO is performed."

  @type t :: %__MODULE__{
          status: non_neg_integer(),
          stdout: String.t(),
          stderr: String.t()
        }

  defstruct status: 0, stdout: "", stderr: ""
end
