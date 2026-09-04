defmodule Taskman.RuntimeConfigTest do
  use ExUnit.Case, async: false

  @required_environment ~w(
    DATABASE_URL
    SECRET_KEY_BASE
    ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET
    PHX_HOST
    RESEND_API_KEY
    MAIL_FROM
  )

  test "test database pool is bounded and independently configurable" do
    with_environment(%{"TEST_DATABASE_POOL_SIZE" => nil}, fn ->
      test_config = Config.Reader.read!("config/test.exs", env: :test, imports: :disabled)

      assert get_in(test_config, [:taskman, Taskman.Repo])[:pool_size] ==
               min(System.schedulers_online() * 2, 16)
    end)

    with_environment(%{"TEST_DATABASE_POOL_SIZE" => "7"}, fn ->
      test_config = Config.Reader.read!("config/test.exs", env: :test, imports: :disabled)

      assert get_in(test_config, [:taskman, Taskman.Repo])[:pool_size] == 7
    end)
  end

  test "production names every missing required environment variable without its value" do
    for name <- @required_environment do
      values = production_environment(%{name => nil})

      with_environment(values, fn ->
        error =
          assert_raise RuntimeError, fn ->
            Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
          end

        assert Exception.message(error) =~ name

        for {environment_name, value} <- values,
            is_binary(value) do
          refute Exception.message(error) =~ value,
                 "#{name} failure exposed #{environment_name}'s value"
        end
      end)
    end
  end

  test "production rejects blank required environment variables without their values" do
    for name <- @required_environment do
      blank = "   "

      with_environment(production_environment(%{name => blank}), fn ->
        error =
          assert_raise RuntimeError, fn ->
            Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
          end

        assert Exception.message(error) =~ name
        refute Exception.message(error) =~ blank
      end)
    end
  end

  test "production configures Resend with its runtime key and sender identity" do
    values = production_environment()

    with_environment(values, fn ->
      runtime_config = Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
      mailer = get_in(runtime_config, [:taskman, Taskman.Mailer])

      assert mailer[:adapter] == Swoosh.Adapters.Resend
      assert mailer[:api_key] == Map.fetch!(values, "RESEND_API_KEY")

      assert get_in(runtime_config, [:taskman, :mail_from]) ==
               {"Taskman", Map.fetch!(values, "MAIL_FROM")}

      prod_config = Config.Reader.read!("config/prod.exs", env: :prod, imports: :disabled)
      assert get_in(prod_config, [:swoosh, :api_client]) == Swoosh.ApiClient.Req
    end)
  end

  test "production canonicalizes a valid MAIL_FROM address for account email delivery" do
    with_environment(production_environment(%{"MAIL_FROM" => "No-Reply@Runtime.Test"}), fn ->
      runtime_config = Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)

      assert get_in(runtime_config, [:taskman, :mail_from]) ==
               {"Taskman", "no-reply@runtime.test"}
    end)
  end

  test "production rejects malformed MAIL_FROM values without exposing them" do
    for mail_from <- [
          "not-an-address",
          "@runtime.test",
          "no-reply@",
          " no-reply@runtime.test",
          "no-reply@runtime.test ",
          "no-reply@runtime.test\r\nBcc: attacker@runtime.test",
          "Taskman <no-reply@runtime.test>",
          "no-reply@runtime.test, attacker@runtime.test"
        ] do
      with_environment(production_environment(%{"MAIL_FROM" => mail_from}), fn ->
        error =
          assert_raise RuntimeError, fn ->
            Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
          end

        assert Exception.message(error) =~ "MAIL_FROM"
        assert Exception.message(error) =~ "single email address"
        refute Exception.message(error) =~ mail_from
      end)
    end
  end

  test "production retains strong and distinct signing secrets" do
    short_secret = String.duplicate("s", 63)

    for name <- ["SECRET_KEY_BASE", "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET"] do
      with_environment(production_environment(%{name => short_secret}), fn ->
        error =
          assert_raise RuntimeError, fn ->
            Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
          end

        assert Exception.message(error) =~ name
        refute Exception.message(error) =~ short_secret
      end)
    end

    shared_secret = String.duplicate("d", 64)

    with_environment(
      production_environment(%{
        "SECRET_KEY_BASE" => shared_secret,
        "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET" => shared_secret
      }),
      fn ->
        error =
          assert_raise RuntimeError, fn ->
            Config.Reader.read!("config/runtime.exs", env: :prod, imports: :disabled)
          end

        assert Exception.message(error) =~ "must be distinct"
        refute Exception.message(error) =~ shared_secret
      end
    )
  end

  defp production_environment(overrides \\ %{}) do
    Map.merge(
      %{
        "DATABASE_URL" => "ecto://runtime-test:runtime-test@localhost/runtime_test",
        "SECRET_KEY_BASE" => String.duplicate("k", 64),
        "ASH_AUTHENTICATION_TOKEN_SIGNING_SECRET" => String.duplicate("a", 64),
        "PHX_HOST" => "runtime.test",
        "RESEND_API_KEY" => "runtime-test-resend-key",
        "MAIL_FROM" => "no-reply@runtime.test"
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
