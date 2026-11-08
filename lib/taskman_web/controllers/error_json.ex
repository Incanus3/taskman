defmodule TaskmanWeb.ErrorJSON do
  alias Plug.Conn.InvalidQueryError
  alias Plug.Parsers.ParseError

  @moduledoc """
  This module is invoked by your endpoint in case of errors on JSON requests.

  See config/config.exs.
  """

  def render("500.json", _assigns) do
    %{error: %{code: "internal_error", message: "Internal Server Error"}}
  end

  def render("400.json", %{reason: reason})
      when is_struct(reason, ParseError) or
             is_struct(reason, InvalidQueryError) do
    %{error: %{code: "invalid_request", message: "Invalid request"}}
  end

  # By default, Phoenix returns the status message from
  # the template name. For example, "404.json" becomes
  # "Not Found".
  def render(template, _assigns) do
    %{errors: %{detail: Phoenix.Controller.status_message_from_template(template)}}
  end
end
