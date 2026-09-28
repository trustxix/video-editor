#!/usr/bin/env bash
# Build the GPL "Corresponding Source" archive for the FFmpeg pinned in
# tools/ffmpeg.lock.json:
#   - FFmpeg itself at ffmpeg_commit
#   - BtbN/FFmpeg-Builds at btbn_commit (the scripts that configured and
#     built it, including their patches)
#   - the source of every library the win64-gpl variant builds, fetched with
#     BtbN's own per-stage download commands (the same ones its download.sh
#     runs inside Docker), with submodules
#   - rav1e's Rust crates, which BtbN's build fetches with cargo
#
# Usage (Git Bash on Windows, or Linux):
#   tools/make_ffmpeg_source.sh [OUT_DIR]        (default OUT_DIR: dist)
# Needs on PATH: git, tar, xz, python, cargo, svn (LAME and Xvid live in
# Subversion). Output: OUT_DIR/ffmpeg-<version>-source.tar (+ .sha256)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Absolute now: the script cd's into its temp dir later, and a relative OUT
# would land inside the dir the EXIT trap deletes.
mkdir -p "${1:-$ROOT/dist}"
OUT="$(cd "${1:-$ROOT/dist}" && pwd)"
PYTHON="$(command -v python || command -v python3)"

for tool in git tar xz cargo svn; do
    command -v "$tool" >/dev/null || { echo "make_ffmpeg_source: '$tool' is not on PATH" >&2; exit 1; }
done

lock() {
    "$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
        "$ROOT/tools/ffmpeg.lock.json" "$1"
}
VERSION="$(lock version)"
FFMPEG_COMMIT="$(lock ffmpeg_commit)"
BTBN_COMMIT="$(lock btbn_commit)"
NAME="ffmpeg-$VERSION-source"

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
PKG="$WORK/$NAME"
mkdir -p "$PKG/deps" "$WORK/dl" "$WORK/bin"

# Byte-exact checkouts: no CRLF conversion (Git for Windows often has
# core.autocrlf=true), long paths allowed. Env config applies to every git
# call below, including ones BtbN's commands and git-sync-deps make, without
# touching the user's git config.
export GIT_CONFIG_COUNT=4
export GIT_CONFIG_KEY_0=core.autocrlf    GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_KEY_1=core.eol         GIT_CONFIG_VALUE_1=lf
export GIT_CONFIG_KEY_2=core.longpaths   GIT_CONFIG_VALUE_2=true
export GIT_CONFIG_KEY_3=advice.detachedHead GIT_CONFIG_VALUE_3=false

# Stand-ins for the helpers in BtbN's Docker base image (images/base/).
cat >"$WORK/bin/git-mini-clone" <<'EOF'
#!/usr/bin/env bash
set -e
git init -q "$3"
git -C "$3" remote add origin "$1"
retry-tool git -C "$3" fetch -q --depth=1 origin "$2"
git -C "$3" checkout -q FETCH_HEAD
EOF
# No per-attempt timeout (BtbN's starts at 120 s, too short for the large
# full clones on a slow link).
cat >"$WORK/bin/retry-tool" <<'EOF'
#!/usr/bin/env bash
for attempt in 1 2 3 4 5; do
    "$@" && exit 0
    echo "retry-tool: attempt $attempt failed: $*" >&2
    sleep $((attempt * 10))
done
exit 1
EOF
# LAME's download step runs autoreconf to pre-generate configure. Generated
# files are not source, and autotools may be absent, so skip it.
cat >"$WORK/bin/autoreconf" <<'EOF'
#!/usr/bin/env bash
echo "make_ffmpeg_source: skipping 'autoreconf $*' (generated files are not source)" >&2
EOF
# shaderc's git-sync-deps runs python3; on Windows that name can be the
# Microsoft Store alias.
printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$PYTHON" >"$WORK/bin/python3"
chmod +x "$WORK/bin/"*
export PATH="$WORK/bin:$PATH"

pack() {  # pack <archive name> <dir>
    tar --exclude=.git --exclude=.svn -C "$2" -cJf "$PKG/$1.tar.xz" .
}

echo "== BtbN/FFmpeg-Builds @ $BTBN_COMMIT"
git-mini-clone https://github.com/BtbN/FFmpeg-Builds.git "$BTBN_COMMIT" "$WORK/btbn"
pack btbn-FFmpeg-Builds "$WORK/btbn"

echo "== FFmpeg @ $FFMPEG_COMMIT"
git-mini-clone https://github.com/FFmpeg/FFmpeg.git "$FFMPEG_COMMIT" "$WORK/ffmpeg"
pack ffmpeg "$WORK/ffmpeg"

MANIFEST="$PKG/MANIFEST.tsv"
printf 'archive\tsha256\tbytes\tsource (from the BtbN stage script)\n' >"$MANIFEST"

cd "$WORK/btbn"
# Defines TARGET/VARIANT/ADDINS_STR and the helpers ffbuild_enabled() uses.
set +u
source util/vars.sh win64 gpl
set -u

for STAGE in scripts.d/*.sh scripts.d/*/*.sh; do
    STAGENAME="$(basename "$STAGE" .sh)"
    [[ -e "$PKG/deps/$STAGENAME.tar.xz" ]] && { echo "duplicate stage name $STAGENAME" >&2; exit 1; }
    (
        set +u  # BtbN's stage scripts aren't written for nounset
        source util/dl_functions.sh
        source "$STAGE"
        ffbuild_enabled || exit 0
        STG="$(ffbuild_dockerdl)"
        [[ -z "$STG" ]] && exit 0

        echo "== $STAGE"
        SOURCES="$(for v in $(compgen -v SCRIPT_); do printf '%s=%s ' "$v" "${!v}"; done)"
        DIR="$WORK/dl/$STAGENAME"
        mkdir -p "$DIR"
        cd "$DIR"
        eval "set -e; $STG"

        if [[ "$STAGENAME" == *-rav1e ]]; then
            # BtbN's build resolves the crates with cargo; ship them too.
            (cd "$DIR" && cargo vendor --locked --versioned-dirs -q vendor >/dev/null)
        fi

        tar --exclude=.git --exclude=.svn -C "$DIR" -cJf "$PKG/deps/$STAGENAME.tar.xz" .
        rm -rf -- "$DIR"
        SUM="$(sha256sum "$PKG/deps/$STAGENAME.tar.xz" | cut -d' ' -f1)"
        SIZE="$(stat -c %s "$PKG/deps/$STAGENAME.tar.xz")"
        printf 'deps/%s.tar.xz\t%s\t%s\t%s\n' "$STAGENAME" "$SUM" "$SIZE" "$SOURCES" >>"$MANIFEST"
    )
done

for top in ffmpeg btbn-FFmpeg-Builds; do
    SUM="$(sha256sum "$PKG/$top.tar.xz" | cut -d' ' -f1)"
    SIZE="$(stat -c %s "$PKG/$top.tar.xz")"
    printf '%s.tar.xz\t%s\t%s\t%s\n' "$top" "$SUM" "$SIZE" \
        "$([[ $top == ffmpeg ]] && echo "https://github.com/FFmpeg/FFmpeg.git $FFMPEG_COMMIT" \
                                || echo "https://github.com/BtbN/FFmpeg-Builds.git $BTBN_COMMIT")" >>"$MANIFEST"
done

cat >"$PKG/README.txt" <<EOF
Corresponding source for the FFmpeg bundled with Video Editor
=============================================================

Binary: ffmpeg.exe / ffprobe.exe version $VERSION
        (BtbN/FFmpeg-Builds, target win64, variant gpl: statically linked,
        configured with --enable-gpl --enable-version3, so GPL version 3 or
        later)

ffmpeg.tar.xz              FFmpeg at commit $FFMPEG_COMMIT
btbn-FFmpeg-Builds.tar.xz  BtbN/FFmpeg-Builds at commit $BTBN_COMMIT, the
                           scripts and patches used to configure and build it
                           (build.sh, scripts.d/, patches/, variants/)
deps/<stage>.tar.xz        source of each library, one per enabled stage in
                           scripts.d/, fetched with that stage's own
                           ffbuild_dockerdl() commands (submodules included);
                           deps/50-rav1e.tar.xz also holds rav1e's crates
                           (vendor/, per its Cargo.lock)
MANIFEST.tsv               SHA256, size and upstream repository/commit of
                           every archive

To rebuild, extract btbn-FFmpeg-Builds.tar.xz and follow its README.md
(./build.sh win64 gpl, which needs Docker). Its download step fetches these
same repositories at these same commits.

Notes:
- Version-control metadata (.git/.svn) is left out.
- LAME's download step also runs autoreconf; that generated output is left
  out (it is not source).
- rav1e: at build time BtbN runs "cargo update cc", which moves only the "cc"
  build helper crate (not linked into the binary) to the newest release.
- Built by tools/make_ffmpeg_source.sh in https://github.com/trustxix/video-editor
EOF

mkdir -p "$OUT"
tar -C "$WORK" -cf "$OUT/$NAME.tar" "$NAME"
HASH="$(sha256sum "$OUT/$NAME.tar" | cut -d' ' -f1 | tr 'a-f' 'A-F')"
printf '%s  %s\n' "$HASH" "$NAME.tar" >"$OUT/$NAME.tar.sha256"
echo "== Wrote $OUT/$NAME.tar ($(du -h "$OUT/$NAME.tar" | cut -f1))"
