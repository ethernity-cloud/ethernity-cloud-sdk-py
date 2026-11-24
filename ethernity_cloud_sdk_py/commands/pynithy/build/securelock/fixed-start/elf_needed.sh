#!/bin/bash
# The ELF files of the root a binary-fs is made of, and the libraries they
# need.
#   elf_needed.sh <root> <listing> [<stage>]
# <root> is the directory the binary-fs is made of: an entry at <root>/a/b is
# the enclave's /a/b. For every regular file below it that is an ELF file, in
# increasing bytewise order of the paths, this writes to <listing> and to
# stdout, with a tab between the fields and <path> the file's path in the
# enclave:
#   ELF <path> <type>        the file: REL, EXEC, DYN, CORE or OTHER (its
#                            e_type), followed by " static" when it has no
#                            dynamic section
# and, in the order of its dynamic section,
#   NEEDED <path> <name>     a DT_NEEDED
#   SONAME <path> <name>     its DT_SONAME
#   RPATH <path> <value>     its DT_RPATH
#   RUNPATH <path> <value>   its DT_RUNPATH
# and, after the lines of every file,
#   MISSING <path> <name>    a needed library the root does not hold
# The root holds a needed library when it is an entry of a directory of the
# file's run path ($ORIGIN and ${ORIGIN} the file's directory) or of /lib,
# /usr/local/lib or /usr/lib, the directories the loader searches; a name
# with a / is the library's path. The names of the C library (lib, one of c,
# pthread, rt, m, dl, util and xnet, and a dot; ld-musl-) are the runtime's
# and never missing.
#
# With <stage>, the root of the file system the ELF files were built on (/ in
# a build stage), a needed library the root does not hold is first copied
# into the root from <stage>/lib, <stage>/usr/local/lib or <stage>/usr/lib,
# the first that holds it, to the same path below the root, with every
# symbolic link on the way to it, and is read in turn; MISSING then names the
# libraries none of the three holds.
#
# A 64-bit little-endian ELF file is read; any other is written as
# ELF <path> UNREAD. A path with a newline is refused.
set -euo pipefail
shopt -s inherit_errexit
export LC_ALL=C

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: elf_needed.sh <root> <listing> [<stage>]" >&2
  exit 2
fi
ROOT=${1%/}
LISTING=$2
STAGE=${3-}
STAGE=${STAGE%/}
MAGIC=$'\177ELF'
NL=$'\n'
LOADER_DIRS=(/lib /usr/local/lib /usr/lib)

refuse() {
  echo "elf_needed.sh: $*" >&2
  exit 1
}

# bytes <file> <offset> <count>: B becomes the file's bytes from the offset,
# two hexadecimal digits each.
bytes() {
  B=($(od -An -v -tx1 -j "$2" -N "$3" "$1"))
}

# le <index> <count>: REPLY becomes the little-endian integer the count
# bytes of B from the index hold.
le() {
  local i
  REPLY=0
  for (( i = $2 - 1; i >= 0; i-- )); do
    REPLY=$(( (REPLY << 8) | 16#${B[$1 + i]} ))
  done
}

# string <file> <offset>: REPLY becomes the NUL-terminated string at the
# offset.
string() {
  local file=$1 at=$2 b ch
  REPLY=
  while :; do
    bytes "$file" "$at" 256
    [ "${#B[@]}" -gt 0 ] || return 0
    for b in "${B[@]}"; do
      [ "$b" != 00 ] || return 0
      printf -v ch '%b' "\\x$b"
      REPLY+=$ch
    done
    at=$(( at + 256 ))
  done
}

# read_elf <file> <its path in the enclave>: the file's lines, to stdout.
read_elf() {
  local file=$1 at=$2 type phoff phentsize phnum i base
  local dynoff=-1 dynsize=0 strtab=-1 stroff=-1
  local -a load_vaddr=() load_offset=() load_size=() tags=() values=()
  bytes "$file" 0 64
  if [ "${#B[@]}" -lt 64 ] || [ "${B[4]}" != 02 ] || [ "${B[5]}" != 01 ]; then
    printf 'ELF\t%s\tUNREAD\n' "$at"
    return 0
  fi
  le 16 2
  case "$REPLY" in 1) type=REL ;; 2) type=EXEC ;; 3) type=DYN ;; 4) type=CORE ;; *) type=OTHER ;; esac
  le 32 8
  phoff=$REPLY
  le 54 2
  phentsize=$REPLY
  le 56 2
  phnum=$REPLY
  if [ "$phnum" -gt 0 ] && [ "$phentsize" -ge 56 ]; then
    bytes "$file" "$phoff" $(( phentsize * phnum ))
    for (( i = 0; i < phnum; i++ )); do
      base=$(( i * phentsize ))
      [ $(( base + 56 )) -le "${#B[@]}" ] || break
      le "$base" 4
      if [ "$REPLY" = 1 ]; then
        le $(( base + 8 )) 8
        load_offset+=("$REPLY")
        le $(( base + 16 )) 8
        load_vaddr+=("$REPLY")
        le $(( base + 32 )) 8
        load_size+=("$REPLY")
      elif [ "$REPLY" = 2 ]; then
        le $(( base + 8 )) 8
        dynoff=$REPLY
        le $(( base + 32 )) 8
        dynsize=$REPLY
      fi
    done
  fi
  if [ "$dynoff" -lt 0 ]; then
    printf 'ELF\t%s\t%s static\n' "$at" "$type"
    return 0
  fi
  printf 'ELF\t%s\t%s\n' "$at" "$type"
  bytes "$file" "$dynoff" "$dynsize"
  for (( i = 0; i + 16 <= ${#B[@]}; i += 16 )); do
    le "$i" 8
    [ "$REPLY" != 0 ] || break
    tags+=("$REPLY")
    [ "$REPLY" != 5 ] || strtab=$i
    le $(( i + 8 )) 8
    values+=("$REPLY")
  done
  [ "$strtab" -ge 0 ] || return 0
  le $(( strtab + 8 )) 8
  strtab=$REPLY
  # The string table's offset in the file: its address in the loaded segment
  # that holds it.
  for (( i = 0; i < ${#load_vaddr[@]}; i++ )); do
    if [ "$strtab" -ge "${load_vaddr[i]}" ] && [ "$strtab" -lt $(( load_vaddr[i] + load_size[i] )) ]; then
      stroff=$(( strtab - load_vaddr[i] + load_offset[i] ))
      break
    fi
  done
  [ "$stroff" -ge 0 ] || return 0
  for (( i = 0; i < ${#tags[@]}; i++ )); do
    case "${tags[i]}" in
      1) type=NEEDED ;;
      14) type=SONAME ;;
      15) type=RPATH ;;
      29) type=RUNPATH ;;
      *) continue ;;
    esac
    string "$file" $(( stroff + values[i] ))
    printf '%s\t%s\t%s\n' "$type" "$at" "$REPLY"
  done
}

# scan: the lines of every ELF file of the root, to stdout.
scan() {
  local file
  while IFS= read -r file; do
    bytes "$file" 0 4
    [ "${B[*]}" = "7f 45 4c 46" ] || continue
    read_elf "$file" "${file#"$ROOT"}"
  done < <({ find "$ROOT" -type f -exec grep -lF -e "$MAGIC" {} + || true; } | sort)
}

# held <path of the file in the enclave> <name> <the file's run paths>:
# whether the root holds the needed library.
held() {
  local origin=${1%/*} name=$2 dir
  local -a dirs
  case "$name" in
    /*) [ -e "$ROOT$name" ] || [ -L "$ROOT$name" ]; return ;;
    */*) return 1 ;;
  esac
  IFS=: read -r -a dirs <<< "$3"
  for dir in "${dirs[@]}" "${LOADER_DIRS[@]}"; do
    dir=${dir//'${ORIGIN}'/"$origin"}
    dir=${dir//'$ORIGIN'/"$origin"}
    case "$dir" in /*) ;; *) continue ;; esac
    if [ -e "$ROOT$dir/$name" ] || [ -L "$ROOT$dir/$name" ]; then
      return 0
    fi
  done
  return 1
}

# missing <listing>: "<path> <name>", tab-separated, for every needed library
# of the listing the root does not hold.
missing() {
  local tag at value
  local -A paths=()
  while IFS=$'\t' read -r tag at value; do
    case "$tag" in
      RPATH|RUNPATH) paths[$at]="${paths[$at]:+${paths[$at]}:}$value" ;;
    esac
  done < "$1"
  while IFS=$'\t' read -r tag at value; do
    [ "$tag" = NEEDED ] || continue
    case "$value" in
      libc.*|libpthread.*|librt.*|libm.*|libdl.*|libutil.*|libxnet.*|ld-musl-*) continue ;;
    esac
    held "$at" "$value" "${paths[$at]:-}" || printf '%s\t%s\n' "$at" "$value"
  done < "$1"
}

# take <path>: copies the stage's library at the path into the root, to the
# same path, with every symbolic link on the way to it.
take() {
  local p=$1 target
  while [ -L "$STAGE$p" ]; do
    mkdir -p "$ROOT${p%/*}"
    cp -Pf "$STAGE$p" "$ROOT$p"
    target=$(readlink "$STAGE$p")
    case "$target" in
      /*) p=$target ;;
      *) p="${p%/*}/$target" ;;
    esac
  done
  mkdir -p "$ROOT${p%/*}"
  cp -f "$STAGE$p" "$ROOT$p"
}

if [ ! -d "$ROOT" ] || [ -L "$ROOT" ]; then
  refuse "the root ${ROOT} is not a directory"
fi
if [ -n "$(find "$ROOT" -name "*${NL}*" | head -c 1)" ]; then
  refuse "an entry's path holds a newline"
fi
trap 'rm -f "$LISTING.part" "$LISTING.missing"' EXIT
while :; do
  scan > "$LISTING.part"
  missing "$LISTING.part" > "$LISTING.missing"
  copied=0
  if [ "$#" -eq 3 ]; then
    while IFS=$'\t' read -r at name; do
      for dir in "${LOADER_DIRS[@]}"; do
        if [ -e "$STAGE$dir/$name" ] && [ ! -e "$ROOT$dir/$name" ]; then
          take "$dir/$name"
          copied=1
          echo "elf_needed.sh: ${dir}/${name}, which ${at} needs, is copied into the root"
          break
        fi
      done
    done < "$LISTING.missing"
  fi
  [ "$copied" = 1 ] || break
done
while IFS=$'\t' read -r at name; do
  printf 'MISSING\t%s\t%s\n' "$at" "$name"
done < "$LISTING.missing" >> "$LISTING.part"
mv "$LISTING.part" "$LISTING"
cat "$LISTING"
