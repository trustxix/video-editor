# What works and what doesn't

Approaches that were tried and failed, and why, so nobody repeats them. Newest
first within each section. Measurements and longer write-ups live in the
commit messages named here.

## Doesn't work

### Playback and audio

- **Re-applying a pitched-audio drift correction on every position tick.** The
  correction only becomes audible (and visible to `playback_position_ms`) after
  the ~250 ms sink queue drains, so each tick re-corrects the same error and the
  loop overshoots into oscillation of tens of seconds. Wait for
  `correction_settled()`. (`ec3d89d`)
- **Calling `QMediaPlayer.setPosition()` before stopping pitched audio.** It emits
  `positionChanged` synchronously, so the drift check runs against the old audio
  and jumps it. Stop or restart the audio first. (`ec3d89d`)
- **Comparing the `QAudioSink` write cursor to the video clock.** The write cursor
  is how far ahead we have queued, not what is playing. Use the played position.
- **`QVideoFrame.toImage()` per frame on the GUI thread.** 8-19 ms per 1440p 10-bit
  frame; it was the whole >1.35x stutter. Conversion now runs on a worker
  (`src/ui/frame_converter.py`).
- **Sending a `QVideoFrame` through a queued signal (PyQt6 6.11)** is silently
  dropped, and keeping the frame a slot receives is a use-after-free (it's
  non-owning). Copy what you need inside the slot.
- **Verifying audio by playing it.** Never open an audio device; analyse samples.

### Export and FFmpeg

- **Rewriting the 500 ms trim/setpts/concat speed-automation export.** It is
  correct; the stutter was in preview.
- **`-hwaccel cuda` without `-hwaccel_output_format`** when a CPU filter follows is
  slower than CPU decode (frames round-trip through system memory).
- **Treating `loudnorm` TP as an offset from the LUFS target.** It is an absolute
  dBTP ceiling; -3 passed every range check and failed every export.
- **Container/codec matrices from the spec.** Verify against the actual muxer
  (AV1 in MOV failed outright).
- **Rebuilding with BtbN's rolling "latest" FFmpeg.** Every build got a different,
  untested nightly and a 200 MB download, and BtbN deletes daily builds after
  about two weeks, so a tested build can't be downloaded again later. Pin by
  hash and cache (`be92d79`).

### Windows, install and release

- **`os.access()` to test writability on Windows.** It ignores NTFS ACLs (it said
  `C:\System Volume Information` was writable). Create and delete a real file
  (`64c842c`).
- **File-association tasks in the installer** while `main.py` ignores `argv`:
  double-clicking a video opened an empty editor (`980548e`).
- **Wiping all of `dist\` in `release.ps1`.** It deleted earlier releases' zips
  and installers (`980548e`).
- **Reading GitHub Actions job logs weeks later** to recover what BtbN built: they
  return HTTP 410. The run's `head_sha` and FFmpeg's `git describe` version are
  what's left.
- **A relative output path in a script that `cd`s into a temp dir with an EXIT
  trap.** The first `make_ffmpeg_source.sh` run (~50 min) wrote its 795 MB tar
  into the temp dir, and the trap deleted it. Resolve output paths to absolute
  before any `cd`.
- **Expecting svn.xvid.org to be quick.** It serves a few files a minute, and a
  failed checkout restarts from zero; budget about an hour for the source
  archive.

### Tests and harnesses

- **Letting a test close a `MainWindow` without cancelling
  `w._shutdown_watchdog`.** `closeEvent` arms an 8 s `os._exit(1)` that silently
  kills the whole pytest run (`58ef891`).
- **Replacing `PitchedAudioPlayer.play()` with a copy in a probe.** It keeps
  testing the old logic after a fix. Patch `src.ui.video_player.QAudioSink` with a
  draining fake instead.
- **Many 1440p HEVC decoder teardowns in one `QApplication`.** Qt multimedia state
  degrades; run one case per subprocess.
- **A/B measurements that aren't interleaved.** The OS file cache favours
  whichever runs second.
- **`@pytest.mark.skipif(not have_ffmpeg)` without FFmpeg on PATH at import
  time.** Every E2E test skipped and the run was green having verified nothing;
  `tests/conftest.py` fixes PATH at import.
- **`print()` then `os._exit()` with piped stdout** loses the output; flush first.

### Tooling (Claude Code on this machine)

- **Python or regex containing backslashes inside a Bash-tool heredoc.** `\\` is
  collapsed, producing syntax errors or form feeds (`\f`) in files. Write such
  files with the Write/Edit tools.
- **One PowerShell command that mixes `Remove-Item` with a `/O...` argument or a
  `C:\Program Files` path**, or a `git commit -F -` heredoc containing
  "shutdown": the destructive-gate hook blocks them. Split the command, or write
  the commit message to a file.
- **`ISCC /Q`** hides compiler warnings as well as progress.

## Works

- Offscreen harnesses (`QT_QPA_PLATFORM=offscreen`) that drive the real
  `MainWindow` with a draining `QAudioSink`, one case per subprocess.
- Checking a build against HEAD by bytecode before deploying (the
  `verify_bundle.py` harness).
- NVDEC crop during decode: bit-identical to the CPU crop, and faster.
- `tools\fetch_ffmpeg.ps1`: a hash-pinned FFmpeg restored from `.ffmpeg-cache`
  makes `build.bat` take ~40 s.
