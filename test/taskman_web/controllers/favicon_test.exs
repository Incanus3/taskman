defmodule TaskmanWeb.FaviconTest do
  use TaskmanWeb.ConnCase, async: true

  test "pages explicitly advertise the SVG favicon and ICO fallback", %{conn: conn} do
    document = conn |> get(~p"/sign-in") |> html_response(200) |> LazyHTML.from_document()

    assert [svg_path] =
             document
             |> LazyHTML.query("head link[rel='icon'][type='image/svg+xml']")
             |> LazyHTML.attribute("href")

    assert svg_path == ~p"/images/favicon.svg"

    assert [ico_path] =
             document
             |> LazyHTML.query("head link[rel='icon'][type='image/x-icon']")
             |> LazyHTML.attribute("href")

    assert ico_path == ~p"/favicon.ico"
  end

  test "the SVG favicon is publicly served as an image", %{conn: conn} do
    conn = get(conn, "/images/favicon.svg")
    assert response(conn, 200) =~ "<svg"
    assert get_resp_header(conn, "content-type") == ["image/svg+xml"]
  end

  test "the fallback is publicly served as an ICO image", %{conn: conn} do
    conn = get(conn, "/favicon.ico")
    assert <<0, 0, 1, 0, _rest::binary>> = response(conn, 200)
    assert get_resp_header(conn, "content-type") == ["image/vnd.microsoft.icon"]
  end

  test "digested ICO filenames are publicly served", %{conn: conn} do
    digest = :crypto.hash(:md5, Integer.to_string(System.unique_integer([:positive])))
    filename = "favicon-#{Base.encode16(digest, case: :lower)}.ico"
    static = Path.join(:code.priv_dir(:taskman), "static")
    path = Path.join(static, filename)
    original = File.read!(Path.join(static, "favicon.ico"))
    File.write!(path, original, [:exclusive])
    on_exit(fn -> File.rm!(path) end)

    conn = get(conn, "/#{filename}?vsn=d")
    assert response(conn, 200) == original
  end
end
