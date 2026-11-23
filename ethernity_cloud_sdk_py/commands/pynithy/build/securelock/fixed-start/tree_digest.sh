#!/bin/bash
# The digests of the fixed start's file tree check (fixed_start.c).
#   tree_digest.sh <tree file> <header> <listing>
# The tree file names the paths the launcher checks, one absolute path per
# line; empty lines and lines starting with # are skipped. For each path, in
# the file's order, this writes the digest of the tree at that path in this
# file system, the one the binary-fs is made from, as fixed_start.c defines
# it, into <header> as FIXED_START_TREE, and the entries each digest covers
# into <listing>. It runs in the build stage that makes the binary-fs, after
# the binary-fs is made.
#
# The digest of a path P is the SHA-256 of these records, written in order:
#   - P does not exist:      'A' P NUL
#   - otherwise, for P and, when P is a directory, every entry below it at
#     every depth, without following symbolic links, in depth-first order with
#     the entries of each directory in increasing bytewise order of their
#     names:
#       a regular file:       'F' path NUL size NUL sha256 NUL
#       a symbolic link:      'L' path NUL target NUL
#       a directory:          no record
#       any other entry:      'O' path NUL
# path is the entry's absolute path, size the number of bytes the file holds
# in decimal, sha256 the SHA-256 of those bytes in lowercase hexadecimal,
# target the link's content.
#
# A path of the tree file is refused unless it is absolute, holds only
# letters, digits and . _ + - / and has no empty, . or .. component; an entry
# whose path or link target holds a newline is refused.
set -euo pipefail
shopt -s inherit_errexit dotglob nullglob
export LC_ALL=C

if [ "$#" -ne 3 ]; then
  echo "usage: tree_digest.sh <tree file> <header> <listing>" >&2
  exit 2
fi
TREE=$1
HEADER=$2
LISTING=$3
NL=$'\n'

refuse() {
  echo "tree_digest.sh: $*" >&2
  exit 1
}

# records <entry>: the records of the entry and, for a directory, of every
# entry below it, to stdout; one line per record to fd 3.
records() {
  local x=$1 c size hash target
  case "$x" in *"$NL"*) refuse "an entry's path holds a newline" ;; esac
  if [ -L "$x" ]; then
    target=$(readlink "$x"; printf .)
    target=${target%.}
    target=${target%"$NL"}
    case "$target" in *"$NL"*) refuse "the target of ${x} holds a newline" ;; esac
    printf 'L%s\0%s\0' "$x" "$target"
    printf 'L %s -> %s\n' "$x" "$target" >&3
  elif [ -d "$x" ]; then
    for c in "$x"/*; do
      records "$c"
    done
  elif [ -f "$x" ]; then
    size=$(stat -c %s "$x")
    hash=$(sha256sum < "$x")
    hash=${hash%% *}
    printf 'F%s\0%s\0%s\0' "$x" "$size" "$hash"
    printf 'F %s %s %s\n' "$size" "$hash" "$x" >&3
  else
    printf 'O%s\0' "$x"
    printf 'O %s\n' "$x" >&3
  fi
}

# digest <path>: the digest of the tree at the path.
digest() {
  local p=$1 d
  if [ ! -e "$p" ] && [ ! -L "$p" ]; then
    d=$(printf 'A%s\0' "$p" | sha256sum)
    printf 'A %s\n' "$p" >&3
  else
    d=$(records "$p" | sha256sum)
  fi
  printf '%s\n' "${d%% *}"
}

paths=()
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  case "$line" in ''|'#'*) continue ;; esac
  case "$line" in
    /*) ;;
    *) refuse "the path ${line} is not absolute" ;;
  esac
  case "$line" in *[!A-Za-z0-9._+/-]*) refuse "the path ${line} holds a character other than letters, digits and . _ + - /" ;; esac
  case "${line}/" in *//*|*/./*|*/../*) refuse "the path ${line} has an empty, . or .. component" ;; esac
  for q in "${paths[@]}"; do
    [ "$q" != "$line" ] || refuse "the path ${line} is named twice"
  done
  paths+=("$line")
done < "$TREE"
[ "${#paths[@]}" -gt 0 ] || refuse "the tree file names no path"

{
  echo "/* Written by tree_digest.sh from $(basename "$TREE"): the digest of each path. */"
  echo "#define FIXED_START_TREE \\"
} > "$HEADER.part"
: > "$LISTING.part"
exec 3>> "$LISTING.part"
for p in "${paths[@]}"; do
  printf '= %s\n' "$p" >&3
  d=$(digest "$p")
  printf '    { "%s", "%s" }, \\\n' "$p" "$d" >> "$HEADER.part"
  printf '= %s %s\n' "$p" "$d" >&3
  echo "tree_digest.sh: ${p} ${d}"
done
exec 3>&-
echo >> "$HEADER.part"
mv "$HEADER.part" "$HEADER"
mv "$LISTING.part" "$LISTING"
