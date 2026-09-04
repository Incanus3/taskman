defmodule Taskman.Terminal do
  @type prompt_result :: String.t() | {:error, :input_unavailable}

  @callback prompt(String.t()) :: prompt_result()
  @callback prompt_secret(String.t()) :: prompt_result()
end
