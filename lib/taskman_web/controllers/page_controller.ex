defmodule TaskmanWeb.PageController do
  use TaskmanWeb, :controller

  def home(conn, _params) do
    render(conn, :home)
  end
end
