defmodule Taskman.Accounts.ApiKey.Changes.Generate do
  @moduledoc """
  Generates Taskman API keys and stores only a digest of the complete credential.

  AshAuthentication's stock API-key change hashes the decoded payload. Taskman
  credentials are deliberately bound to their exact textual representation, so
  the project-owned change hashes the complete `tm_...` value instead.
  """

  use Ash.Resource.Change

  @impl true
  def change(changeset, opts, _) do
    prefix = to_string(Keyword.fetch!(opts, :prefix))

    if String.match?(prefix, ~r/[^a-z0-9]/) do
      raise ArgumentError,
            "#{inspect(prefix)} contains invalid characters. Must contain only `a-z0-9`"
    end

    random_bytes = base62_safe_bytes()
    id = Ecto.UUID.bingenerate()
    payload = random_bytes <> id

    plaintext =
      "#{prefix}_#{AshAuthentication.Base.encode62(payload)}_#{AshAuthentication.Base.encode62(:erlang.crc32(payload))}"

    changeset
    |> Ash.Changeset.force_change_attribute(:id, id)
    |> Ash.Changeset.force_change_attribute(
      Keyword.fetch!(opts, :hash),
      :crypto.hash(:sha256, plaintext)
    )
    |> Ash.Changeset.after_action(fn _changeset, result ->
      {:ok, Ash.Resource.set_metadata(result, %{plaintext_api_key: plaintext})}
    end)
  end

  defp base62_safe_bytes do
    case :crypto.strong_rand_bytes(32) do
      # Base62 encoding is an integer representation and cannot preserve a
      # leading null byte. Regenerate until the payload has a canonical form.
      <<0, _::binary>> -> base62_safe_bytes()
      bytes -> bytes
    end
  end
end
