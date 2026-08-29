defmodule Taskman.CLI.EndToEndTest do
  use Taskman.DataCase, async: false

  test "creates and inspects a Project through the loopback API" do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"

    create =
      Taskman.CLI.run([
        "projects",
        "create",
        "--name",
        "HTTP smoke",
        "--directory",
        File.cwd!(),
        "--api-url",
        api_url,
        "--json"
      ])

    assert create.status == 0
    assert create.stderr == ""
    assert String.ends_with?(create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "primary_directory" => directory
             }
           } = Jason.decode!(create.stdout)

    assert directory == File.cwd!()

    show =
      Taskman.CLI.run([
        "projects",
        "show",
        Integer.to_string(id),
        "--api-url",
        api_url,
        "--json"
      ])

    assert show.status == 0
    assert show.stderr == ""
    assert String.ends_with?(show.stdout, "\n")

    assert Jason.decode!(show.stdout) == %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "primary_directory" => File.cwd!()
             }
           }
  end
end
