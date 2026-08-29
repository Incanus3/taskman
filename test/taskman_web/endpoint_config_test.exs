defmodule TaskmanWeb.EndpointConfigTest do
  use ExUnit.Case, async: false

  test "production endpoint defaults to loopback while honoring host and port" do
    with_environment(
      %{
        "DATABASE_URL" => "ecto://postgres:postgres@localhost/taskman",
        "SECRET_KEY_BASE" => "test-secret",
        "PHX_HOST" => "taskman.test",
        "PORT" => "4567"
      },
      fn ->
        endpoint =
          "config/runtime.exs"
          |> Config.Reader.read!(env: :prod, imports: :disabled)
          |> get_in([:taskman, TaskmanWeb.Endpoint])

        assert endpoint[:http][:ip] == {127, 0, 0, 1}
        assert endpoint[:http][:port] == 4567
        assert endpoint[:url][:host] == "taskman.test"
      end
    )
  end

  defp with_environment(values, fun) do
    previous = Map.new(values, fn {name, _value} -> {name, System.get_env(name)} end)

    Enum.each(values, fn {name, value} -> System.put_env(name, value) end)

    try do
      fun.()
    after
      Enum.each(previous, fn
        {name, nil} -> System.delete_env(name)
        {name, value} -> System.put_env(name, value)
      end)
    end
  end
end
