# FFmpeg source code

Video Editor ships `ffmpeg.exe` and `ffprobe.exe` in its `ffmpeg\` folder.
They are FFmpeg as built by the BtbN/FFmpeg-Builds project (target win64,
variant gpl): configured with `--enable-gpl --enable-version3` and statically
linked with GPL-compatible libraries, so they are licensed under the GNU
General Public License, **version 3 or (at your option) any later version**.
The license text is `ffmpeg\licenses\LICENSE.txt`. The exact build is named
on the `Version:` line of `ffmpeg\BUNDLE_INFO.txt`.

## Getting the source

The complete corresponding source is published in the same place as the app,
on the GitHub release you downloaded it from:

  https://github.com/trustxix/video-editor/releases

Every release that ships a given FFmpeg build has an asset named
`ffmpeg-<version>-source.tar` (plus a `.sha256`), where `<version>` is the
`Version:` line of `ffmpeg\BUNDLE_INFO.txt`, for example
`ffmpeg-N-126390-g9fc8c785e2-20260903-source.tar`. It holds FFmpeg at the
exact commit, the BtbN build scripts and patches at the commit that built it,
and the source of every library linked into the binaries. Its `README.txt`
explains the layout, and `MANIFEST.tsv` lists each part's upstream repository,
commit and SHA256.

## Written offer

For at least three years after we last distribute a version of Video Editor,
anyone who has it can also get that source from us, at no charge beyond the
cost of physically performing the transfer: open an issue at
https://github.com/trustxix/video-editor/issues titled
"FFmpeg source request" and include the `Version:` line from your
`ffmpeg\BUNDLE_INFO.txt`.
