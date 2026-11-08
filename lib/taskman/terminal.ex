defmodule Taskman.Terminal do
  @moduledoc "Terminal input callbacks for visible and secret prompts."

  @type prompt_result :: String.t() | {:error, :input_unavailable}

  @callback prompt(String.t()) :: prompt_result()
  @callback prompt_secret(String.t()) :: prompt_result()
end
