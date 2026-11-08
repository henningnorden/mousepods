#!/bin/sh
# Cross-compile the MousePods TrackIR / FreeTrack DLLs (64- and 32-bit) with zig.
# zig is downloaded to a private cache and checked against its sha256; nothing is installed system-wide.
set -eu
cd "$(dirname "$0")"
ZV=0.17.1
ZSHA=3581863e3cbcfd0fce0efc682ce7e9e93da96668a977791d356a26d2a7e9b17a
# Private, user-owned cache (never a shared /tmp path): the archive is verified before every
# extraction, and an existing extraction is only reused if its recorded archive hash matches.
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/mousepods-build"
mkdir -p "$CACHE" && chmod 700 "$CACHE"
ZDIR="$CACHE/zig-x86_64-linux-$ZV"
Z="$ZDIR/zig"
if [ ! -x "$Z" ] || [ "$(cat "$ZDIR/.verified-sha256" 2>/dev/null)" != "$ZSHA" ]; then
  rm -rf "$ZDIR"
  TMP=$(mktemp -d "$CACHE/dl.XXXXXX")
  trap 'rm -rf "$TMP"' EXIT
  curl -sSL -o "$TMP/zig.tar.xz" "https://ziglang.org/download/$ZV/zig-x86_64-linux-$ZV.tar.xz"
  echo "$ZSHA  $TMP/zig.tar.xz" | sha256sum -c -
  tar -C "$TMP" -xJf "$TMP/zig.tar.xz"
  mv "$TMP/zig-x86_64-linux-$ZV" "$ZDIR"
  echo "$ZSHA" > "$ZDIR/.verified-sha256"
fi
mkdir -p out
F="-O2 -Wall -Wno-unused-parameter -Wno-unused-variable"
"$Z" cc -target x86_64-windows-gnu $F -shared -o out/NPClient64.dll npclient.c -ladvapi32
"$Z" cc -target x86_64-windows-gnu $F -shared -o out/freetrackclient64.dll freetrackclient.c -ladvapi32
# 32-bit: __stdcall exports would be decorated (_NP_GetData@4); the .def files export plain names
"$Z" cc -target x86-windows-gnu $F -shared -o out/NPClient.dll npclient.c npclient.def -ladvapi32
"$Z" cc -target x86-windows-gnu $F -shared -o out/freetrackclient.dll freetrackclient.c freetrackclient.def -ladvapi32
rm -f out/*.pdb out/*.lib
ls -l out
