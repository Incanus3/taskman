"""Shared-lock remote lifecycle snapshot transaction."""

from __future__ import annotations

from .remote_locking import REMOTE_LOCK_FRAMING


_SNAPSHOT_BODY = r'''for p in "$deployment_root" "$managed_root" "$release_root" "$backup_root"; do safe_path "$p"; done
safe_name() { case "$1" in ''|*[!A-Za-z0-9_./+-]*) return 1;; esac; return 0; }
safe_release_id() { printf '%s\n' "$1" | grep -Eq '^[0-9]+[.][0-9]+[.][0-9]+([-+][0-9A-Za-z.-]+)?-[0-9a-f]{12}-ubuntu26[.]04-amd64-otp27[.]3[.]4[.]6$'; }
record_path_id() { record_filename=${1##*/}; record_filename=${record_filename#release-}; record_filename=${record_filename%.json}; safe_release_id "$record_filename" || return 1; printf '%s' "$record_filename"; }
record_identifier() {
  record_category=$1; record_filename=$2
  case "$record_category:$record_filename" in
    releases:release-*.json) record_path_id "$record_filename" ;;
    activations:activation-*.json) record_identifier_value=${record_filename%.json}; printf '%s\n' "$record_identifier_value" | grep -Eq '^activation-[0-9a-f]{32}$' && printf '%s' "$record_identifier_value" ;;
    backups:backup-*.json) record_identifier_value=${record_filename%.json}; printf '%s\n' "$record_identifier_value" | grep -Eq '^backup-[0-9a-f]{32}$' && printf '%s' "$record_identifier_value" ;;
    adoptions:adoption-*.json) record_identifier_value=${record_filename#adoption-}; record_identifier_value=${record_identifier_value%.json}; safe_release_id "$record_identifier_value" && printf '%s' "$record_identifier_value" ;;
    *) return 1 ;;
  esac
}
warning_add() {
  warning_prefix=$1; warning_name=$2
  if safe_name "$warning_name"; then warning="$warning_prefix$warning_name"; else warning="${warning_prefix}unsafe-name"; fi
  warnings="${warnings}${warnings:+|}$warning"
}
safe_optional_dir "$deployment_root"; safe_optional_dir "$managed_root"; safe_optional_dir "$release_root"; safe_optional_dir "$backup_root"
for root in releases activations backups adoptions adoption-transactions manifests; do safe_optional_dir "$deployment_root/$root"; done
warnings=
append_file() { safe_file "$1" || exit 10; test "$first" = 1 || printf ','; cat "$1"; first=0; }
emit_direct_records() {
  category=$1; directory="$deployment_root/$category"
  if test -d "$directory"; then
    for record in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$record" || test -L "$record" || continue
      name=${record##*/}
      record_identifier "$category" "$name" >/dev/null && append_file "$record"
    done
  fi
}
complete_adoption_bundle() {
  adoption_marker=$1; adoption_marker_name=${adoption_marker##*/}; bundle_id=${adoption_marker_name#adoption-}; bundle_id=${bundle_id%.json}
  safe_release_id "$bundle_id" || exit 10
  safe_file "$adoption_marker" || exit 10
  bundle="$deployment_root/adoption-transactions/adoption-$bundle_id"
  safe_dir "$bundle" || exit 10
  test "$(stat -c %a -- "$bundle")" = 750 || exit 10
  safe_file "$bundle/release.json" || exit 10
  safe_file "$bundle/activation.json" || exit 10
}
validate_adoption_release_path() {
  adoption_marker=$1
  adoption_release_path=$(sed -n 's/.*"release_path":"\([^"]*\)".*/\1/p' "$adoption_marker")
  safe_path "$adoption_release_path"
  case "$adoption_release_path" in "$release_root"/*) ;; *) exit 10;; esac
  safe_dir "$adoption_release_path" || exit 10
}
emit_adopted_records() {
  entry=$1; directory="$deployment_root/adoptions"
  if test -d "$directory"; then
    for marker in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$marker" || test -L "$marker" || continue
      name=${marker##*/}
      record_identifier adoptions "$name" >/dev/null || continue
      complete_adoption_bundle "$marker"; validate_adoption_release_path "$marker"; append_file "$bundle/$entry"
    done
  fi
}
emit_adoption_markers() {
  directory="$deployment_root/adoptions"
  if test -d "$directory"; then
    for marker in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$marker" || test -L "$marker" || continue
      name=${marker##*/}
      record_identifier adoptions "$name" >/dev/null || continue
      complete_adoption_bundle "$marker"; validate_adoption_release_path "$marker"; append_file "$marker"
    done
  fi
}
adopted_id() {
  adoption_wanted=$1; adoption_directory="$deployment_root/adoptions"
  test -d "$adoption_directory" || return 1
  for marker in "$adoption_directory"/* "$adoption_directory"/.[!.]* "$adoption_directory"/..?*; do
    test -e "$marker" || test -L "$marker" || continue
    adoption_marker_name=${marker##*/}; record_identifier adoptions "$adoption_marker_name" >/dev/null || continue
    complete_adoption_bundle "$marker"
    test "$bundle_id" = "$adoption_wanted" && return 0
  done
  return 1
}
require_declared_release_paths() {
  directory="$deployment_root/releases"
  if test -d "$directory"; then
    for record in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$record" || test -L "$record" || continue
      name=${record##*/}; release_id=$(record_identifier releases "$name") || continue
      safe_file "$record" || exit 10
      safe_dir "$release_root/$release_id" || exit 10
    done
  fi
  directory="$deployment_root/adoptions"
  if test -d "$directory"; then
    for marker in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$marker" || test -L "$marker" || continue
      name=${marker##*/}; record_identifier adoptions "$name" >/dev/null || continue
      complete_adoption_bundle "$marker"; validate_adoption_release_path "$marker"
    done
  fi
}
emit_manifests() {
  first=1; printf '{'; directory="$deployment_root/releases"
  if test -d "$directory"; then
    for record in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$record" || test -L "$record" || continue
      name=${record##*/}; release_id=$(record_identifier releases "$name") || continue
      safe_file "$record" || exit 10
      adopted_id "$release_id" && continue
      manifest="$deployment_root/manifests/release-$release_id.json"
      safe_file "$manifest" || exit 10
      test "$first" = 1 || printf ','
      printf '"%s":' "$release_id"; cat "$manifest"; first=0
    done
  fi
  printf '}'
}
record_dump_path() {
  dump_path=$(sed -n 's/.*"dump_path":"\([^"]*\)".*/\1/p' "$1")
  safe_path "$dump_path"
  case "$dump_path" in "$backup_root"/*) ;; *) exit 10;; esac
  printf '%s' "$dump_path"
}
emit_dump_states() {
  first=1; printf '{'; directory="$deployment_root/backups"
  if test -d "$directory"; then
    for record in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$record" || test -L "$record" || continue
      name=${record##*/}; record_identifier backups "$name" >/dev/null || continue
      safe_file "$record" || exit 10
      dump_path=$(record_dump_path "$record")
      dump_state=stale
      if test -f "$dump_path" && test ! -L "$dump_path"; then dump_state=present; fi
      test "$first" = 1 || printf ','
      printf '"%s":"%s"' "$dump_path" "$dump_state"; first=0
    done
  fi
  printf '}'
}
direct_release_path_declared() {
  declared_path=$1; declared_name=${declared_path##*/}
  safe_release_id "$declared_name" || return 1
  declared_record="$deployment_root/releases/release-$declared_name.json"
  test -e "$declared_record" || test -L "$declared_record" || return 1
  declared_actual=$(record_identifier releases "${declared_record##*/}") || return 1
  test "$declared_actual" = "$declared_name"
}
declared_release_path() {
  declared_path=$1
  direct_release_path_declared "$declared_path" && return 0
  directory="$deployment_root/adoptions"; test -d "$directory" || return 1
  for marker in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
    test -e "$marker" || test -L "$marker" || continue
    name=${marker##*/}; record_identifier adoptions "$name" >/dev/null || continue
    complete_adoption_bundle "$marker"; validate_adoption_release_path "$marker"
    test "$adoption_release_path" = "$declared_path" && return 0
  done
  return 1
}
inventory_deployment_root() {
  test -d "$deployment_root" || return 0
  for entry in "$deployment_root"/* "$deployment_root"/.[!.]* "$deployment_root"/..?*; do
    test -e "$entry" || test -L "$entry" || continue
    name=${entry##*/}
    case "$name" in releases|activations|backups|adoptions|adoption-transactions|manifests) ;; *) warning_add "unexpected deployment-root entry: " "$name";; esac
  done
}
inventory_record_roots() {
  for category in releases activations backups adoptions; do
    directory="$deployment_root/$category"; test -d "$directory" || continue
    for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      test -e "$entry" || test -L "$entry" || continue
      name=${entry##*/}; record_identifier "$category" "$name" >/dev/null || warning_add "unrecognized deployment storage entry: $category/" "$name"
    done
  done
}
inventory_transactions() {
  directory="$deployment_root/adoption-transactions"; test -d "$directory" || return 0
  for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
    test -e "$entry" || test -L "$entry" || continue
    name=${entry##*/}; candidate=${name#adoption-}
    if test "$candidate" = "$name" || ! safe_release_id "$candidate" || ! adopted_id "$candidate"; then warning_add "unrecognized lifecycle transaction: " "$name"; fi
  done
}
inventory_manifests() {
  directory="$deployment_root/manifests"; test -d "$directory" || return 0
  for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
    test -e "$entry" || test -L "$entry" || continue
    name=${entry##*/}; candidate=${name#release-}; candidate=${candidate%.json}
    if test "$candidate" = "$name" || ! safe_release_id "$candidate" || ! direct_release_path_declared "$release_root/$candidate" || adopted_id "$candidate"; then warning_add "unrecognized manifest entry: " "$name"; fi
  done
}
inventory_release_root() {
  test -d "$release_root" || return 0
  for entry in "$release_root"/* "$release_root"/.[!.]* "$release_root"/..?*; do
    test -e "$entry" || test -L "$entry" || continue
    declared_release_path "$entry" && continue
    if test -d "$entry" && test ! -L "$entry"; then warning_add "unrecognized release directory: " "${entry##*/}"; else warning_add "unrecognized release entry: " "${entry##*/}"; fi
  done
}
known_dump_path() {
  wanted=$1; directory="$deployment_root/backups"; test -d "$directory" || return 1
  for record in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
    test -e "$record" || test -L "$record" || continue
    name=${record##*/}; record_identifier backups "$name" >/dev/null || continue
    safe_file "$record" || exit 10
    dump_path=$(record_dump_path "$record")
    test "$dump_path" = "$wanted" && return 0
  done
  return 1
}
inventory_backup_root() {
  test -d "$backup_root" || return 0
  for entry in "$backup_root"/* "$backup_root"/.[!.]* "$backup_root"/..?*; do
    test -e "$entry" || test -L "$entry" || continue
    known_dump_path "$entry" || warning_add "orphan backup dump: " "${entry##*/}"
  done
}
emit_warnings() {
  first=1; pending=$warnings; printf '['
  while test -n "$pending"; do
    warning=${pending%%|*}
    if test "$warning" = "$pending"; then pending=; else pending=${pending#*|}; fi
    test "$first" = 1 || printf ','
    printf '"%s"' "$warning"; first=0
  done
  printf ']'
}
require_declared_release_paths
inventory_deployment_root; inventory_record_roots; inventory_transactions; inventory_manifests; inventory_release_root; inventory_backup_root
printf '{"schema_version":1,"records":{"releases":['; first=1; emit_direct_records releases; emit_adopted_records release.json; printf ']'
printf ',"activations":['; first=1; emit_direct_records activations; emit_adopted_records activation.json; printf ']'
printf ',"backups":['; first=1; emit_direct_records backups; printf ']'
printf ',"adoptions":['; first=1; emit_adoption_markers; printf ']'
printf '},"current_target":'
if test -L "$managed_root/current"; then current=$(readlink -f -- "$managed_root/current") || exit 10; printf '"%s"' "$current"; else printf 'null'; fi
printf ',"manifests":'; emit_manifests
printf ',"dump_states":'; emit_dump_states
printf ',"warnings":'; emit_warnings; printf '}\n'
'''

REMOTE_SNAPSHOT_TRANSACTION = (
    "set -eu\nLC_ALL=C; export LC_ALL\n"
    "deployment_root=$1; managed_root=$2; release_root=$3; backup_root=$4; lock_root=$5; operation=$6; timeout_ms=$7; owner_uid=$8; lock_mode=shared\n"
    + REMOTE_LOCK_FRAMING
    + _SNAPSHOT_BODY
)


__all__ = ["REMOTE_SNAPSHOT_TRANSACTION"]
