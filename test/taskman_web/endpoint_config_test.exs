defmodule TaskmanWeb.EndpointConfigTest do
  use ExUnit.Case, async: false

  test "production endpoint defaults to loopback while honoring host and port" do
    with_environment(
      production_environment(%{"PORT" => "4567"}),
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

  test "production requires distinct authentication and mail configuration" do
    for name <- [
          "DATABASE_URL",
          "SECRET_KEY_BASE",
          "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET",
          "PHX_HOST",
          "RESEND_API_KEY",
          "MAIL_FROM"
        ] do
      with_environment(production_environment(%{name => nil}), fn ->
        assert_raise RuntimeError, ~r/environment variable #{name} is missing/, fn ->
          Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
        end
      end)
    end

    with_environment(
      production_environment(%{"ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET" => "test-secret"}),
      fn ->
        assert_raise RuntimeError, ~r/must be distinct/, fn ->
          Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
        end
      end
    )
  end

  test "production enables secure cookies and HTTPS with HSTS" do
    config = Config.Reader.read!("config/prod.exs", env: :prod, imports: :disabled)

    assert get_in(config, [:taskman, :secure_cookies])
    assert get_in(config, [:taskman, :force_ssl?])
  end

  defp production_environment(overrides) do
    Map.merge(
      %{
        "DATABASE_URL" => "ecto://postgres:postgres@localhost/taskman",
        "SECRET_KEY_BASE" => "test-secret",
        "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET" => "test-authentication-secret",
        "PHX_HOST" => "taskman.test",
        "RESEND_API_KEY" => "re_test-key",
        "MAIL_FROM" => "no-reply@taskman.test"
      },
      overrides
    )
  end

  defp with_environment(values, fun) do
    previous = Map.new(values, fn {name, _value} -> {name, System.get_env(name)} end)

    Enum.each(values, fn
      {name, nil} -> System.delete_env(name)
      {name, value} -> System.put_env(name, value)
    end)

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
