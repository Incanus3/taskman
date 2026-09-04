defmodule Taskman.Accounts.User.Status do
  use Ash.Type.Enum, values: [:pending, :active, :disabled]
end
