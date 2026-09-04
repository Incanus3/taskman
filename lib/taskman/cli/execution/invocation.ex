defmodule Taskman.CLI.Execution.Invocation do
  @moduledoc "A parsed command invocation ready for dispatch."

  alias Taskman.CLI.Registry.Command

  @type t :: %__MODULE__{
          command: Command.t(),
          arguments: %{optional(atom()) => term()},
          options: %{optional(atom()) => term()},
          globals: %{optional(atom()) => term()}
        }

  defstruct command: nil, arguments: %{}, options: %{}, globals: %{}
end
