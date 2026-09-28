# Privacy Policy

**Effective:** 2026-09-28
**Application:** Video Editor (desktop, Windows)

## Summary

This app collects **nothing** about you. It runs entirely on your local
computer. The only automatic network request is the update check below
(on by default, and you can turn it off in **Settings → General → Privacy**).
Official builds contain no crash-report sender.

## What we do not collect

- Your video files, video content, audio, or any media you process
- File paths or filenames
- The contents of your projects or settings
- Your IP address (the app does not initiate any HTTP request that would
  reveal it, except for the optional features below)
- Identifiers, advertising IDs, or analytics events

## Optional features

### Update check (default: ON)

When the app starts (and when you choose **Help → Check for Updates**), it
makes a single HTTPS GET request to GitHub's public release API:
  `https://api.github.com/repos/trustxix/video-editor/releases/latest`

This request includes only:
- Your IP address (visible to GitHub by virtue of the connection)
- A `User-Agent` header containing your installed app version
  (e.g., `VideoEditor/0.1.0`)

It does NOT include any filenames, project paths, settings, video data,
or other identifying information.

You can turn off the startup check in **Settings → General → Privacy →
Check for updates**. **Help → Check for Updates** still works when you
choose it.

### Crash reports (not in official builds)

Official builds never transmit crash reports. When the app crashes it only
writes a local dump (see "Files written to disk" below).

The source code can send reports to Sentry, but only in a build that
includes the `sentry-sdk` library, only when the `SENTRY_DSN` environment
variable is set on your computer, and only if you set `crash_reports` to
`true` in `settings.json` (default `false`). Crashes that happen before your
settings load are never sent. Such a report contains:
- The Python exception type and message (with file paths sanitized — your
  Windows username is removed before transmission)
- The Python traceback (also sanitized)
- Your operating system name and version (e.g., `Windows-10.0.22631`)
- The installed app version

It does NOT contain any video files, video content, settings values,
or filenames.

### "Report a Bug" (manual, never automatic)

When you click **Help → Report a Bug**, the app:
1. Reads the most recent ~200 lines of `editor.log`
2. Sanitizes paths (removes your Windows username)
3. Copies a markdown-formatted report to your clipboard
4. Saves the same report as `VideoEditor-bug-report-<date>-<time>.md` on
   your Desktop
5. Opens the project's GitHub new-issue page in your default browser

Nothing is transmitted automatically — you choose what to send and where.

## Files written to disk

All data this app stores is on your local computer, in one config folder:
`<install dir>/config` when the install folder is writable, otherwise
`%LOCALAPPDATA%/Video Editor/config` (for example, an all-users install under
Program Files). The app logs which one it uses at startup.
- `<config>/settings.json` — your preferences
- `<config>/editor.log` (and `.1`, `.2`, `.3` rotations) —
  rotating log file, max ~8 MB total. Paths in the log are sanitized to
  remove your username.
- `<config>/crashes/*.json` — local crash dumps written when
  uncaught exceptions occur (the newest 20 are kept). Official builds never
  transmit them. You can delete this folder any time.
- `<config>/export_error.log` (and `.1`) — the FFmpeg command line and
  error output of failed exports, including file names, with your username
  removed from paths. Rotated at ~1 MB.
- `VideoEditor-bug-report-*.md` on your Desktop — only when you use
  **Help → Report a Bug**.

## Children

This app is not directed to children under 13 and does not knowingly
collect data from anyone.

## Changes to this policy

If this policy changes, the new version will ship with the next app
release and be visible at `<install dir>/PRIVACY.md`. The "Effective" date
at the top will be updated.

## Contact

Open an issue at the project's issue tracker (linked from the README).
