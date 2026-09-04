"""Exclusive remote manual-adoption transaction."""

from __future__ import annotations

from .remote_locking import REMOTE_LOCK_FRAMING


_ADOPTION_BODY = r'''for p in "$deployment_root" "$managed_root" "$release_root" "$backup_root" "$caddy_config"; do safe_path "$p"; done
for port in "$application_port" "$distribution_port" "$database_port"; do case "$port" in ''|*[!0-9]*) exit 2;; esac; test "$port" -ge 1 && test "$port" -le 65535 || exit 2; done
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
authoritative_lifecycle_entry() {
  category=$1; name=$2
  case "$category:$name" in
    releases:release-*.json) identifier=${name#release-}; identifier=${identifier%.json}; safe_release_id "$identifier" ;;
    activations:activation-*.json) identifier=${name%.json}; printf '%s\n' "$identifier" | grep -Eq '^activation-[0-9a-f]{32}$' ;;
    adoptions:adoption-*.json) identifier=${name#adoption-}; identifier=${identifier%.json}; safe_release_id "$identifier" ;;
    *) return 1 ;;
  esac
}
refuse_existing_lifecycle_state() {
  for category in releases activations adoptions; do
    directory="$deployment_root/$category"
    safe_optional_dir "$directory"
    test -d "$directory" || continue
    for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$entry" || test -L "$entry" || continue
      authoritative_lifecycle_entry "$category" "${entry##*/}" && exit 10
    done
  done
}
loopback_listener() {
  listener_port=$1
  ss -H -ltn "sport = :$listener_port" | awk -v port="$listener_port" '
    $4 == "127.0.0.1:" port || $4 == "[::1]:" port { found = 1; next }
    { unsafe = 1 }
    END { exit (!found || unsafe) }
  '
}
migration_fingerprints() {
  directory=$1; first=1
  printf '['
  for migration in "$directory"/*; do
    test -e "$migration" || test -L "$migration" || continue
    test -f "$migration" && test ! -L "$migration" && test "$(stat -c %u -- "$migration")" = "$owner_uid" || exit 10
    filename=${migration##*/}
    printf '%s\n' "$filename" | grep -Eq '^[0-9]{14}_[a-z0-9_]+[.]exs$' || exit 10
    migration_sha=$(sha256sum -- "$migration" | awk '{print $1}')
    printf '%s\n' "$migration_sha" | grep -Eq '^[0-9a-f]{64}$' || exit 10
    test "$first" = 1 || printf ','
    printf '{"filename":"%s","sha256":"%s"}' "$filename" "$migration_sha"
    first=0
  done
  printf ']'
}
validate_release_tree() {
  test -z "$(find -P "$selected" -type l -print -quit)" || exit 10
  test -z "$(find -P "$selected" ! -type d ! -type f -print -quit)" || exit 10
  test -z "$(find -P "$selected" ! -user "$owner_uid" -print -quit)" || exit 10
  test -z "$(find -P "$selected" -perm /022 -print -quit)" || exit 10
  test -z "$(find -P "$selected" -perm /7000 -print -quit)" || exit 10
}
tree_digest() {
  (
    cd "$selected"
    printf 'entries\0'
    find -P . -printf '%y|%m|%u|%p\0' | LC_ALL=C sort -z
    printf 'contents\0'
    find -P . -type f -print0 | LC_ALL=C sort -z | xargs -r -0 sha256sum -z --
  ) | sha256sum | cut -d' ' -f1
}
safe_optional_dir "$managed_root"; safe_optional_dir "$release_root"
test -L "$managed_root/current" || exit 10
selected=$(readlink -f -- "$managed_root/current") || exit 10
case "$selected" in "$release_root"/*) ;; *) exit 10;; esac
safe_dir "$selected" || exit 10
test -x "$selected/bin/server" && test -d "$selected/lib" && test -d "$selected/releases" || exit 10
validate_release_tree
curl -fsS --max-time 5 "http://127.0.0.1:$application_port/healthz" | grep -Fx ready >/dev/null || exit 10
systemctl is-active --quiet taskman.service || exit 10
systemctl is-active --quiet caddy.service || exit 10
main_pid=$(systemctl show --property=MainPID --value taskman.service) || exit 10
case "$main_pid" in ''|0|*[!0-9]*) exit 10;; esac
main_executable=$(readlink -f -- "/proc/$main_pid/exe") || exit 10
case "$main_executable" in "$selected"/*) ;; *) exit 10;; esac
test -f "$caddy_config" && test ! -L "$caddy_config" || exit 10
grep -Eq "^[[:space:]]*reverse_proxy[[:space:]]+127[.]0[.]0[.]1:$application_port([[:space:]]|$)" "$caddy_config" || exit 10
loopback_listener "$application_port" || exit 10
loopback_listener "$distribution_port" || exit 10
loopback_listener "$database_port" || exit 10
epmd_listeners=$(ss -H -ltn 'sport = :4369') || exit 10
test -z "$epmd_listeners" || exit 10
app_file=$(find -P "$selected/lib" -path "$selected/lib/taskman-*/ebin/taskman.app" -type f -print -quit)
test -n "$app_file" || exit 10
version=$(grep -o 'vsn,"[0-9][0-9A-Za-z.+-]*"' "$app_file" | head -n 1 | sed 's/vsn,"//;s/"//')
migration_root="$selected/lib/taskman-$version/priv/repo/migrations"
test -d "$migration_root" && test ! -L "$migration_root" || exit 10
migrations=$(migration_fingerprints "$migration_root")
content=$(tree_digest)
printf '%s\n' "$content" | grep -Eq '^[0-9a-f]{64}$' || exit 10
prefix=$(printf '%s' "$content" | cut -c1-12)
release_id="$version-$prefix-ubuntu26.04-amd64-otp27.3.4.6"
safe_release_id "$release_id" || exit 10
refuse_existing_lifecycle_state
case "$adoption_mode" in
  inspect)
    test "$expected_release_id:$expected_content_sha256:$expected_migrations_b64" = "-:-:-" || exit 2
    printf '{"schema_version":1,"candidate":{"schema_version":1,"release_id":"%s","release_path":"%s","content_sha256":"%s","application_version":"%s","migrations":%s}}\n' "$release_id" "$selected" "$content" "$version" "$migrations"
    exit 0
    ;;
  adopt)
    case "$expected_release_id" in
      -)
        test "$expected_content_sha256:$expected_migrations_b64" = "-:-" || exit 2
        ;;
      *)
        safe_release_id "$expected_release_id" || exit 2
        printf '%s\n' "$expected_content_sha256" | grep -Eq '^[0-9a-f]{64}$' || exit 2
        expected_migrations=$(printf '%s' "$expected_migrations_b64" | base64 -d) || exit 2
        test "$release_id" = "$expected_release_id" &&
          test "$content" = "$expected_content_sha256" &&
          test "$migrations" = "$expected_migrations" ||
          exit 10
        ;;
    esac
    ;;
  *) exit 2 ;;
esac
timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ensure_safe_dir "$deployment_root"
ensure_safe_dir "$deployment_root/adoptions"
ensure_safe_dir "$deployment_root/adoption-transactions"
marker="$deployment_root/adoptions/adoption-$release_id.json"
bundle="$deployment_root/adoption-transactions/adoption-$release_id"
test ! -e "$marker" && test ! -L "$marker" || exit 10
test ! -e "$bundle" && test ! -L "$bundle" || exit 10
stage=$(mktemp -d "$deployment_root/adoption-transactions/.adoption.XXXXXXXX")
marker_tmp=$(mktemp "$deployment_root/adoptions/.adoption.XXXXXXXX")
bundle_published=0; marker_published=0
cleanup() {
  rm -f -- "$marker_tmp" || :
  if test "$marker_published" = 0; then
    rm -rf -- "$stage" || :
    if test "$bundle_published" = 1; then rm -rf -- "$bundle" || :; fi
  fi
  release_lifecycle_lock
}
trap cleanup EXIT
trap 'cleanup; exit 1' HUP INT TERM
activation_id="activation-$(printf '%s' "$content" | cut -c1-32)"
printf '{"schema_version":1,"release_id":"%s","artifact_sha256":null,"installed_at":"%s","activated_at":"%s","previous_release_id":null,"backup_id":null,"migration_policy":"adopted"}\n' "$release_id" "$timestamp" "$timestamp" >"$stage/release.json"
printf '{"schema_version":1,"activation_id":"%s","previous_release_id":null,"candidate_release_id":"%s","activated_at":"%s","backup_id":null,"migration_policy":"adopted"}\n' "$activation_id" "$release_id" "$timestamp" >"$stage/activation.json"
printf '{"schema_version":1,"release_id":"%s","adopted_at":"%s","release_path":"%s","content_sha256":"%s","application_version":"%s","source_revision":"unknown","artifact_sha256":"unknown","migrations":%s}\n' "$release_id" "$timestamp" "$selected" "$content" "$version" "$migrations" >"$marker_tmp"
chown "$owner_uid:$owner_uid" -- "$stage/release.json" "$stage/activation.json" "$marker_tmp"
chmod 600 -- "$stage/release.json" "$stage/activation.json" "$marker_tmp"
chmod 750 -- "$stage"
sync -f "$stage/release.json"; sync -f "$stage/activation.json"; sync -f "$marker_tmp"; sync -f "$stage"
mv -T -n -- "$stage" "$bundle"
if test -e "$stage" || test -L "$stage"; then exit 10; fi
bundle_published=1
sync -f "$deployment_root/adoption-transactions"
ln -- "$marker_tmp" "$marker"
marker_published=1
sync -f "$deployment_root/adoptions"
rm -f -- "$marker_tmp"
release_lifecycle_lock
trap - EXIT HUP INT TERM
printf '{"schema_version":1,"adoption":{"schema_version":1,"release_id":"%s","adopted_at":"%s","release_path":"%s","content_sha256":"%s","application_version":"%s","source_revision":"unknown","artifact_sha256":"unknown","migrations":%s}}\n' "$release_id" "$timestamp" "$selected" "$content" "$version" "$migrations"
'''

REMOTE_ADOPTION_TRANSACTION = (
    "set -eu\nLC_ALL=C; export LC_ALL\n"
    "deployment_root=$1; managed_root=$2; release_root=$3; backup_root=$4; lock_root=$5; timeout_ms=$6; application_port=$7; distribution_port=$8; database_port=$9; caddy_config=${10}; adoption_mode=${11}; expected_release_id=${12}; expected_content_sha256=${13}; expected_migrations_b64=${14}; owner_uid=0; operation=adoption-$adoption_mode; lock_mode=exclusive\n"
    + REMOTE_LOCK_FRAMING
    + _ADOPTION_BODY
)


__all__ = ["REMOTE_ADOPTION_TRANSACTION"]
