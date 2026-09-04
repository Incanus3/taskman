defmodule Taskman.CaddyConfigTest do
  use ExUnit.Case, async: true

  @moduletag :tmp_dir

  @invalid_hostnames [
    "",
    "localhost",
    "taskman.example.com",
    "TASKMAN.EXAMPLE.COM",
    "taskman.test",
    "taskman.invalid",
    "taskman.example",
    "taskman.local",
    "taskman.home.arpa",
    "taskman.onion",
    "taskman",
    "127.0.0.1",
    "0.0.0.0",
    "999.999",
    "tasks.123",
    "-taskman.acme.org",
    "taskman-.acme.org",
    "taskman..acme.org",
    "taskman_acme.org"
  ]

  test "renders a normalized public hostname into the Caddy template" do
    assert {rendered, 0} = render("TASKS.ACME.ORG")

    assert rendered == """
           tasks.acme.org {
           \treverse_proxy 127.0.0.1:4000
           }
           """
  end

  test "rejects malformed and reserved hostnames" do
    too_long_label = String.duplicate("a", 64) <> ".acme.org"

    for hostname <- [too_long_label | @invalid_hostnames] do
      assert {message, status} = render(hostname)
      assert status != 0, "expected #{inspect(hostname)} to be rejected"
      assert message =~ "valid public DNS hostname"
    end
  end

  test "rejects a file that is not the expected Caddy template", %{tmp_dir: tmp_dir} do
    template = Path.join(tmp_dir, "Caddyfile")
    File.write!(template, "unrelated.example.org {\n\trespond \"ok\"\n}\n")

    assert {message, status} = render("tasks.acme.org", template)
    assert status != 0
    assert message =~ "expected exactly one taskman.example.com site"
  end

  defp render(hostname, template \\ caddyfile()) do
    System.cmd("sh", [renderer(), hostname, template], stderr_to_stdout: true)
  end

  defp renderer, do: Path.join(root(), "ops/caddy/render-caddyfile")
  defp caddyfile, do: Path.join(root(), "ops/caddy/Caddyfile")
  defp root, do: Path.expand("../..", __DIR__)
end
