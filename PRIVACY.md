# Privacy Policy

**Effective:** 2026-04-25
**Application:** Video Editor (desktop, Windows)

## Summary

This app collects **nothing by default**. It runs entirely on your local
computer. Optional features that send data over the network are listed
below — each is OFF by default unless noted otherwise, and each can be
disabled in **Settings → General**.

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

You can disable this in **Settings → General → Check for updates**.

### Crash reports (default: OFF)

If you opt in via **Settings → General → Send anonymous crash reports**, the
app will transmit minimal crash diagnostics on uncaught exceptions:
- The Python exception type and message (with file paths sanitized — your
  Windows username is removed before transmission)
- The Python traceback (also sanitized)
- Your operating system name and version (e.g., `Windows-10.0.22631`)
- The installed app version

It does NOT transmit any video files, video content, settings values,
or filenames. Crashes that occur **before** the user enables this option
are never transmitted.

### "Report a Bug" (manual, never automatic)

When you click **Help → Report a Bug**, the app:
1. Reads the most recent ~200 lines of `editor.log`
2. Sanitizes paths (removes your Windows username)
3. Copies a markdown-formatted report to your clipboard
4. Opens your default browser to a GitHub issue URL or your default mail
   client

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
- `<config>/.installation_id` — random per-install identifier
  used only to salt the single-instance mutex name (no PII)
- `<config>/crashes/*.json` — local crash dumps written when
  uncaught exceptions occur. These are NOT transmitted unless you enable
  the optional Crash Reports feature above. You can delete this folder
  any time.

## Children

This app is not directed to children under 13 and does not knowingly
collect data from anyone.

## Changes to this policy

If this policy changes, the new version will ship with the next app
release and be visible at `<install dir>/PRIVACY.md`. The "Effective" date
at the top will be updated.

## Contact

Open an issue at the project's issue tracker (linked from the README).
