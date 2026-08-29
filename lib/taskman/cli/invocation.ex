defmodule Taskman.CLI.Invocation do
  @moduledoc "A parsed command invocation ready for dispatch."

  alias Taskman.CLI.Command

  @type t :: %__MODULE__{
          command: Command.t(),
          arguments: %{optional(atom()) => term()},
          options: %{optional(atom()) => term()},
          globals: %{optional(atom()) => term()}
        }

  defstruct command: nil, arguments: %{}, options: %{}, globals: %{}
end
