defmodule TaskmanWeb.ErrorJSONTest do
  use TaskmanWeb.ConnCase, async: true

  test "renders 404" do
    assert TaskmanWeb.ErrorJSON.render("404.json", %{}) == %{errors: %{detail: "Not Found"}}
  end

  test "renders generic 400 responses" do
    assert TaskmanWeb.ErrorJSON.render("400.json", %{}) == %{errors: %{detail: "Bad Request"}}
  end

  test "renders 500" do
    assert TaskmanWeb.ErrorJSON.render("500.json", %{}) ==
             %{error: %{code: "internal_error", message: "Internal Server Error"}}
  end
end
