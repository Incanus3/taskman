defmodule Taskman.Terminal do
  @callback prompt(String.t()) :: String.t()
  @callback prompt_secret(String.t()) :: String.t()
end
