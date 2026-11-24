#!/bin/bash
# The digest of the fixed start's file tree check (fixed_start.c).
#   tree_digest.sh <root> <header> <listing>
# <root> is the directory the binary-fs is made of: an entry at <root>/a/b is
# the enclave's /a/b. This writes the digest of the root, as fixed_start.c
# defines it, into <header> as FIXED_START_ROOT_DIGEST, and the entries the
# digest covers into <listing>, a line each. It runs in the build stage that
# makes the binary-fs, on the directory the binary-fs is made of.
#
# The digest is the SHA-256 of these records, written depth first from the
# root, the entries of each directory in increasing bytewise order of their
# names, without following symbolic links:
#       a directory:          'D' path NUL, then the records of its entries
#       a regular file:       'F' path NUL size NUL sha256 NUL
#       a symbolic link:      'L' path NUL target NUL
# path is the entry's path in the enclave, size the number of bytes the file
# holds in decimal, sha256 the SHA-256 of those bytes in lowercase
# hexadecimal, target the link's content; the root itself has no record.
#
# A root is refused when it is not a directory, when it holds a directory
# without an entry or an entry that is neither a directory, a regular file
# nor a symbolic link (the binary-fs holds regular files and symbolic links
# alone, so the enclave would see neither), when an entry is more than 64
# names below it or has a path of 4096 bytes or more in the enclave (the
# launcher refuses both), and when a path or a link's target holds a newline.
set -euo pipefail
shopt -s inherit_errexit dotglob nullglob
export LC_ALL=C

if [ "$#" -ne 3 ]; then
  echo "usage: tree_digest.sh <root> <header> <listing>" >&2
  exit 2
fi
ROOT=$1
HEADER=$2
LISTING=$3
NL=$'\n'
DEPTH_MAX=64
PATH_MAX=4096

refuse() {
  echo "tree_digest.sh: $*" >&2
  exit 1
}

# records <directory> <its path in the enclave> <its depth>: the records of
# the directory's entries and of everything below them, to stdout; one line
# per record to fd 3.
records() {
  local dir=$1 at=$2 depth=$(( $3 + 1 )) x p size hash target
  local entries=("$dir"/*)
  [ "${#entries[@]}" -gt 0 ] || refuse "the directory ${at:-/} holds no entry, and the binary-fs holds no empty directory"
  for x in "${entries[@]}"; do
    p="${at}/${x##*/}"
    case "$p" in *"$NL"*) refuse "an entry's path holds a newline" ;; esac
    [ "$depth" -le "$DEPTH_MAX" ] || refuse "${p} is more than ${DEPTH_MAX} names below the root"
    [ "${#p}" -lt "$PATH_MAX" ] || refuse "the path ${p} takes ${PATH_MAX} bytes or more"
    if [ -L "$x" ]; then
      target=$(readlink "$x"; printf .)
      target=${target%.}
      target=${target%"$NL"}
      case "$target" in *"$NL"*) refuse "the target of ${p} holds a newline" ;; esac
      printf 'L%s\0%s\0' "$p" "$target"
      printf 'L %s -> %s\n' "$p" "$target" >&3
    elif [ -d "$x" ]; then
      printf 'D%s\0' "$p"
      printf 'D %s\n' "$p" >&3
      records "$x" "$p" "$depth"
    elif [ -f "$x" ]; then
      size=$(stat -c %s "$x")
      hash=$(sha256sum < "$x")
      hash=${hash%% *}
      printf 'F%s\0%s\0%s\0' "$p" "$size" "$hash"
      printf 'F %s %s %s\n' "$size" "$hash" "$p" >&3
    else
      refuse "${p} is neither a directory, a regular file nor a symbolic link, and the binary-fs holds no such entry"
    fi
  done
}

if [ ! -d "$ROOT" ] || [ -L "$ROOT" ]; then
  refuse "the root ${ROOT} is not a directory"
fi
trap 'rm -f "$HEADER.part" "$LISTING.part"' EXIT
: > "$LISTING.part"
exec 3>> "$LISTING.part"
d=$(records "${ROOT%/}" "" 0 | sha256sum)
d=${d%% *}
printf '= / %s\n' "$d" >&3
exec 3>&-
{
  echo "/* Written by tree_digest.sh: the digest of the root the binary-fs is made of. */"
  echo "#define FIXED_START_ROOT_DIGEST \"${d}\""
} > "$HEADER.part"
mv "$HEADER.part" "$HEADER"
mv "$LISTING.part" "$LISTING"
echo "tree_digest.sh: / ${d}"
