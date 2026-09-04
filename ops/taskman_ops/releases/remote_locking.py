"""Shared shell framing for remote lifecycle transactions.

These strings are passed as one argv member to a privileged POSIX shell. The
framing owns safe lock state and holder metadata; domain modules compose it.
"""

from __future__ import annotations

REMOTE_ATOMIC_WRITE = r'''set -eu
directory=$1
filename=$2
target="$directory/$filename"
if [ -e "$directory" ] || [ -L "$directory" ]; then
  test -d "$directory"
  test ! -L "$directory"
  test "$(stat -c %u -- "$directory")" = 0
  test "$(stat -c %a -- "$directory")" = 750
else
  install -d -o root -g root -m 750 -- "$directory"
fi
test ! -e "$target"
test ! -L "$target"
temporary=$(mktemp "$directory/.$filename.XXXXXXXX")
cleanup() { rm -f -- "$temporary"; }
trap cleanup EXIT HUP INT TERM
cat > "$temporary"
chown root:root -- "$temporary"
chmod 600 -- "$temporary"
sync -f "$temporary"
ln -- "$temporary" "$target"
sync -f "$directory"
rm -f -- "$temporary"
trap - EXIT HUP INT TERM
'''


REMOTE_LOCK_FRAMING = r'''safe_path() { case "$1" in /*) ;; *) exit 2 ;; esac; case "$1" in *[!A-Za-z0-9_./+-]*|*..*) exit 2 ;; esac; }
safe_dir() {
  test -d "$1" && test ! -L "$1" && test "$(stat -c %u -- "$1")" = "$owner_uid" || return 1
  directory_mode=$(stat -c %a -- "$1")
  case "$directory_mode" in
    [0-7][0-7][0-7]) directory_permissions=$directory_mode ;;
    0[0-7][0-7][0-7]) directory_permissions=${directory_mode#0} ;;
    *) return 1 ;;
  esac
  case "$directory_permissions" in [0-7][2367][0-7]|[0-7][0-7][2367]) return 1;; esac
}
safe_file() { test -f "$1" && test ! -L "$1" && test "$(stat -c %u:%a -- "$1")" = "$owner_uid:600"; }
safe_optional_dir() { if test -e "$1" || test -L "$1"; then safe_dir "$1" || exit 10; fi; }
ensure_safe_dir() {
  if test -e "$1" || test -L "$1"; then safe_dir "$1" || exit 10
  else install -d -o "$owner_uid" -g "$owner_uid" -m 750 -- "$1"; safe_dir "$1" || exit 10
  fi
}
prepare_lock_file() {
  lock_file=$1
  if test -e "$lock_file" || test -L "$lock_file"; then safe_file "$lock_file" || exit 10
  else (umask 077; : >"$lock_file"); chown "$owner_uid:$owner_uid" "$lock_file"; chmod 600 "$lock_file"; safe_file "$lock_file" || exit 10
  fi
}
safe_path "$lock_root"
printf '%s\n' "$operation" | grep -Eq '^[a-z][a-z-]{0,63}$' || exit 2
case "$timeout_ms" in ''|*[!0-9]*) exit 2;; esac
case "$lock_mode" in shared|exclusive) ;; *) exit 2;; esac
ensure_safe_dir "$lock_root"
prepare_lock_file "$lock_root/lifecycle.lock"
metadata="$lock_root/lifecycle.lock.meta"
prepare_lock_file "$metadata"
exec 9>"$lock_root/lifecycle.lock"
exec 8<>"$metadata"
metadata_valid() {
  case "$1" in ''|*[!0-9a-f]*) return 1;; esac
  test "${#1}" = 32 || return 1
  printf '%s\n' "$2" | grep -Eq '^[a-z][a-z-]{0,63}$' || return 1
  case "$3" in ''|*[!0-9]*) return 1;; esac; test "$3" -gt 0 || return 1
  printf '%s\n' "$4" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' || return 1
  case "$5" in shared|exclusive) ;; *) return 1;; esac
}
metadata_store() {
  if test -n "$metadata_live"; then printf '%s\n' "$metadata_live" >"$metadata"; else : >"$metadata"; fi
  sync -f "$metadata"; sync -f "$lock_root"
}
metadata_retain_except() {
  metadata_excluded=$1
  metadata_live=$(while IFS='|' read -r metadata_token metadata_operation metadata_pid metadata_started metadata_mode metadata_extra || test -n "${metadata_token}${metadata_operation}${metadata_pid}${metadata_started}${metadata_mode}${metadata_extra}"; do
    test -z "$metadata_extra" || exit 10
    metadata_valid "$metadata_token" "$metadata_operation" "$metadata_pid" "$metadata_started" "$metadata_mode" || exit 10
    if kill -0 "$metadata_pid" 2>/dev/null && test "$metadata_token" != "$metadata_excluded"; then
      printf '%s|%s|%s|%s|%s\n' "$metadata_token" "$metadata_operation" "$metadata_pid" "$metadata_started" "$metadata_mode"
    fi
  done <"$metadata")
  metadata_store
}
metadata_append() {
  metadata_retain_except ""
  if test -n "$metadata_live"; then
    metadata_live="$metadata_live
$1|$2|$3|$4|$5"
  else
    metadata_live="$1|$2|$3|$4|$5"
  fi
  metadata_store
}
metadata_holder() {
  while IFS='|' read -r metadata_token metadata_operation metadata_pid metadata_started metadata_mode metadata_extra || test -n "${metadata_token}${metadata_operation}${metadata_pid}${metadata_started}${metadata_mode}${metadata_extra}"; do
    test -z "$metadata_extra" || exit 10
    metadata_valid "$metadata_token" "$metadata_operation" "$metadata_pid" "$metadata_started" "$metadata_mode" || exit 10
    printf '{"schema_version":1,"holder":{"operation":"%s","pid":%s,"started_at":"%s","mode":"%s"}}\n' "$metadata_operation" "$metadata_pid" "$metadata_started" "$metadata_mode"
    return 0
  done <"$metadata"
  printf '{"schema_version":1,"holder":null}\n'
}
lock_token=
lock_timeout_seconds=$(( (timeout_ms + 999) / 1000 ))
lock_deadline=$(( $(date +%s) + lock_timeout_seconds ))
while :; do
  flock -x 8
  metadata_retain_except ""
  if { test "$lock_mode" = shared && flock -sn 9; } || { test "$lock_mode" = exclusive && flock -xn 9; }; then
    lock_token=$(printf '%s' "$(date +%s%N)-$$" | sha256sum | cut -c1-32)
    lock_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    metadata_append "$lock_token" "$operation" "$$" "$lock_started" "$lock_mode"
    flock -u 8
    break
  fi
  lock_holder=$(metadata_holder)
  flock -u 8
  if test "$(date +%s)" -ge "$lock_deadline"; then printf '%s' "$lock_holder"; exit 12; fi
  sleep 1
done
release_lifecycle_lock() {
  test -n "$lock_token" || return 0
  flock -x 8 || return 0
  flock -u 9
  metadata_retain_except "$lock_token" || :
  flock -u 8 || :
  lock_token=
}
trap 'release_lifecycle_lock' EXIT
trap 'exit 1' HUP INT TERM
'''

__all__ = ["REMOTE_ATOMIC_WRITE", "REMOTE_LOCK_FRAMING"]
