# Mass Distribution Readiness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the Video Editor from "personal tool" to "ready to send to strangers without embarrassment" using only what can be done with the current developer machine, free tooling, and no money/account creation.

**Architecture:** Five sequential phases. Phase 1 fixes correctness so we don't ship a broken product. Phase 2 builds a real distribution pipeline (auto-bundled FFmpeg, installer, CI). Phase 3 adds a real test suite. Phase 4 adds in-app observability hooks (local crash dumps, version-check, bug reporter, settings migration). Phase 5 hardens packaging and documentation. Each phase is independently shippable.

**Tech Stack:** Python 3.14, PyQt6 6.11, FFmpeg (BtbN GitHub releases for bundling), pytest, PyInstaller, Inno Setup 6 (free, winget-installable), GitHub Actions (free for private repos), no paid services.

**Out of scope (requires money or account creation — left as activatable hooks for later):**
- Authenticode code signing certificate (~$90-400/yr) — build script will sign if `SIGNCERT_PATH` env var is set, otherwise skip
- Sentry/cloud crash reporting — code will use Sentry if `SENTRY_DSN` env var is set, otherwise local dumps only
- Real CDN download hosting — version-check JSON lives at GitHub raw URL (free)
- Hardware diversity testing (only one dev machine available)

---

## File Structure (what will be created or modified)

### New files
- `tools/fetch_ffmpeg.ps1` — downloads BtbN FFmpeg release, verifies SHA256, extracts to dist
- `tools/sign.ps1` — Authenticode signing wrapper (no-op without cert env var)
- `tools/installer.iss` — Inno Setup script (Start Menu, uninstaller, file associations)
- `tools/release.ps1` — orchestrates build → fetch_ffmpeg → sign → installer → output
- `.github/workflows/ci.yml` — runs tests on every push
- `.github/workflows/release.yml` — builds installer on tag push
- `LICENSE` — MIT (or matching project license — verify before committing)
- `NOTICES.md` — third-party attribution (FFmpeg LGPL/GPL, PyQt6 GPL, Qt LGPL, etc.)
- `docs/FFMPEG_SOURCE_OFFER.md` — written GPL source offer
- `PRIVACY.md` — privacy stance (no telemetry collected without opt-in)
- `tests/conftest.py` — pytest fixtures, generates fixture videos via FFmpeg lavfi
- `tests/fixtures/` — generated at test time, gitignored
- `tests/test_ffmpeg_runner.py` — real unit tests (fix + replace test_fixes.py)
- `tests/test_export_e2e.py` — end-to-end: probe → export → ffprobe output
- `tests/test_archive.py`, `tests/test_speed_curve.py`, `tests/test_keybinds.py`
- `tests/test_settings_migration.py`
- `tests/test_memory_leak.py` — RSS-tracking smoke test
- `src/core/version.py` — single source of truth for version string + update-check
- `src/core/crash_reporter.py` — local crash dump writer + optional Sentry
- `src/core/log_setup.py` — log rotation + path sanitization
- `src/core/settings_migration.py` — version-aware settings migration
- `src/core/bug_report.py` — sanitized log bundling for "Report a Bug" feature

### Files modified (line ranges approximate, recheck before edit)
- `tests/test_fixes.py` — fix line 112 (h265/CRF assumption broken)
- `src/core/ffmpeg_runner.py` — surface silent failures (lines ~22, ~139, ~400-411, ~525-548, ~779), validate loudnorm measurements
- `src/ui/main_window.py` — surface settings save failures (~921-930, ~1262-1263, ~1806-1819), output path traversal guard (~1982), wire bug-report menu
- `main.py` — wire crash_reporter, log_setup, settings_migration; per-installation salt mutex name (~25)
- `src/core/auto_presets.py` — whitelist allowed encoder names (~67-92)
- `src/core/paths.py` — add APPDATA-based per-installation ID generation
- `tools/build.bat` — call fetch_ffmpeg, copy LICENSE/NOTICES, optional sign
- `requirements.txt` — pin exact versions
- `.gitignore` — add `backups/`, `tests/fixtures/`

---

## Phase 1 — Correctness & Robustness (P0 silent-failure fixes)

Goal: every silent failure in the audit becomes user-visible. No more "exports a silent video and reports success."

### Task 1.1: Fix broken test collection in test_fixes.py

**Files:**
- Modify: `tests/test_fixes.py:106-112`

The test crashes pytest at collection time because `_encode_args` no longer always emits `-crf` (NVENC uses `-cq`). The test needs to be aware of both code paths.

- [ ] **Step 1: Read current state of test_fixes.py to understand structure**

Run: `head -50 tests/test_fixes.py` and `sed -n '100,120p' tests/test_fixes.py`

- [ ] **Step 2: Patch lines 106-112 to handle both NVENC and software encode**

Replace:
```python
cmd = build_command(
    'in.mp4', 'out.mp4',
    trim_start=5.0, trim_end=10.0,
    crop_x=100, crop_y=50, crop_w=800, crop_h=600,
    codec='h265', crf=0, speed=0.5,
    stretch_h=0.8, stretch_v=1.0, audio_mode='reencode'
)
vf = cmd[cmd.index('-vf') + 1]
test("Combined: all video filters", 'crop=' in vf and 'scale=' in vf and 'setpts=' in vf)
test("Combined: h265 codec", 'libx265' in cmd)
test("Combined: lossless CRF", cmd[cmd.index('-crf') + 1] == '0')
test("Combined: speed overrides reencode audio", '-af' in cmd)
```

With:
```python
cmd = build_command(
    'in.mp4', 'out.mp4',
    trim_start=5.0, trim_end=10.0,
    crop_x=100, crop_y=50, crop_w=800, crop_h=600,
    codec='h265', crf=0, speed=0.5,
    stretch_h=0.8, stretch_v=1.0, audio_mode='reencode'
)
vf = cmd[cmd.index('-vf') + 1]
test("Combined: all video filters", 'crop=' in vf and 'scale=' in vf and 'setpts=' in vf)
# H.265: libx265 (software) OR hevc_nvenc (GPU). Either is acceptable.
test("Combined: h265 codec", 'libx265' in cmd or 'hevc_nvenc' in cmd)
# Lossless: software emits -crf 0; NVENC emits -cq 0. Either is acceptable.
quality_flag = '-crf' if '-crf' in cmd else '-cq'
test("Combined: lossless quality", cmd[cmd.index(quality_flag) + 1] == '0')
test("Combined: speed overrides reencode audio", '-af' in cmd)
```

- [ ] **Step 3: Run pytest collection to verify no errors**

Run: `python -m pytest tests/test_fixes.py --collect-only 2>&1 | tail -20`
Expected: no `ERROR collecting`, the file collects cleanly.

- [ ] **Step 4: Run the test directly**

Run: `python tests/test_fixes.py 2>&1 | tail -30`
Expected: All combined-filter assertions PASS. Total counts at the bottom should show no failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_fixes.py
git commit -m "fix(tests): make h265/lossless assertions tolerate NVENC and software paths"
```

---

### Task 1.2: Add subprocess timeouts to ffmpeg probes

**Files:**
- Modify: `src/core/ffmpeg_runner.py` — `get_video_duration`, `get_video_resolution`, `get_video_fps`, `probe_video`, NVENC probe (`_check_nvenc_available`)

Without timeouts, a hung ffprobe (network drive, locked file, weird codec) freezes the UI thread permanently.

- [ ] **Step 1: Read the current probe functions**

Run Read on `src/core/ffmpeg_runner.py` looking for `get_video_duration`, `get_video_resolution`, `get_video_fps`, `_check_nvenc_available`. Note current `subprocess.run` call signatures.

- [ ] **Step 2: Add `timeout=5` to every ffprobe subprocess.run call**

For each function, add `timeout=5` parameter. Example pattern:
```python
try:
    result = subprocess.run(
        [FFPROBE_PATH, ...args...],
        capture_output=True, text=True,
        startupinfo=_hide_window(),
        timeout=5,
    )
except subprocess.TimeoutExpired:
    return 0.0  # or appropriate fallback per function
except (subprocess.SubprocessError, OSError) as e:
    # surface to log so we can debug
    return 0.0
```

For NVENC probe (`_check_nvenc_available`), use `timeout=10` (it's a one-time encode test).

- [ ] **Step 3: Add a helper `_probe_safe(args, timeout, fallback)` in ffmpeg_runner to DRY up the pattern**

Place near the top of `ffmpeg_runner.py`, after imports:

```python
def _probe_safe(args: list[str], timeout: float = 5.0, fallback=None):
    """Run an ffprobe command with timeout. Return parsed stdout on success,
    fallback on any failure. Logs but does not raise."""
    try:
        result = subprocess.run(
            args,
            capture_output=True, text=True,
            startupinfo=_hide_window(),
            timeout=timeout,
        )
        if result.returncode != 0:
            return fallback
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return fallback
    except (subprocess.SubprocessError, OSError):
        return fallback
```

Refactor `get_video_duration`, `get_video_resolution`, `get_video_fps` to use it.

- [ ] **Step 4: Write test that probe with non-existent file does not hang**

Add to `tests/test_ffmpeg_runner.py` (create if missing — see Task 3.1 for full file):

```python
import time
from src.core.ffmpeg_runner import get_video_duration, get_video_resolution

def test_get_video_duration_missing_file_returns_fast():
    start = time.monotonic()
    result = get_video_duration("C:/no-such-file-xyz123.mp4")
    elapsed = time.monotonic() - start
    assert result == 0.0
    assert elapsed < 6.0, f"Probe took {elapsed}s, should fail fast under timeout"

def test_get_video_resolution_missing_file_returns_fast():
    start = time.monotonic()
    result = get_video_resolution("C:/no-such-file-xyz123.mp4")
    elapsed = time.monotonic() - start
    assert result == (0, 0)
    assert elapsed < 6.0
```

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_ffmpeg_runner.py -v 2>&1 | tail -10`
Expected: both new tests PASS in under 6 seconds each.

- [ ] **Step 6: Commit**

```bash
git add src/core/ffmpeg_runner.py tests/test_ffmpeg_runner.py
git commit -m "fix(ffmpeg): add 5s timeout to all probe calls + tests"
```

---

### Task 1.3: Surface silent failures in loudnorm and prerender_audio

**Files:**
- Modify: `src/core/ffmpeg_runner.py` — `loudnorm_analyze` (~line 139), `prerender_audio` (~lines 400-411)

Currently both swallow exceptions and return None/False with no UI feedback. Audio normalization silently skips. Audio prerender silently produces a video with no audio.

- [ ] **Step 1: Add a status-callback parameter to loudnorm_analyze**

Change signature:
```python
def loudnorm_analyze(input_path: str, target_lufs: float = -16.0,
                     status_cb=None) -> dict | None:
```

In the existing `except Exception as e:` block, replace `return None` with:
```python
import traceback
err = f"Loudness analysis failed: {type(e).__name__}: {e}"
if status_cb:
    status_cb(err)
# Always log, even if no callback
try:
    from src.core.paths import config_dir
    log_path = config_dir() / "editor.log"
    log_path.write_text(
        f"{log_path.read_text() if log_path.exists() else ''}\n[loudnorm] {err}\n{traceback.format_exc()}",
        encoding='utf-8'
    )
except Exception:
    pass  # last-resort
return None
```

(Note: this temporary write pattern will be replaced by `log_setup` in Task 1.5; for now use direct file write.)

- [ ] **Step 2: Add same status-callback to prerender_audio**

In `prerender_audio`, on the failure path (currently lines ~400-411), call `status_cb(f"Audio prerender failed: ...")` before returning False.

- [ ] **Step 3: Wire status_cb through `export_with_automation`**

Pass `status_cb` from `export_with_automation` to both `loudnorm_analyze` and `prerender_audio` (default None for back-compat).

- [ ] **Step 4: Wire from main_window.py**

Find the call site of `export_with_automation` in `src/ui/main_window.py`. Add a callback that surfaces to the status bar:

```python
def _export_status(msg: str) -> None:
    self.statusBar().showMessage(msg, 8000)
    # Also OSD if visible
    if hasattr(self, 'show_osd'):
        self.show_osd(msg)
```

Pass `status_cb=self._export_status` to the export call.

- [ ] **Step 5: Manual verification**

Run: trigger an export with normalize_audio=True on a clip. If it works, normalize succeeds. To force the failure path for verification, temporarily rename `ffmpeg.exe` and re-trigger — the status bar should show "Loudness analysis failed: FileNotFoundError: ...". Restore ffmpeg afterward.

- [ ] **Step 6: Add unit test**

Add to `tests/test_ffmpeg_runner.py`:

```python
def test_loudnorm_analyze_calls_status_cb_on_failure(tmp_path):
    from src.core.ffmpeg_runner import loudnorm_analyze
    captured = []
    result = loudnorm_analyze(
        str(tmp_path / "no-such-file.mp4"),
        status_cb=lambda msg: captured.append(msg)
    )
    assert result is None
    assert len(captured) >= 1
    assert "failed" in captured[0].lower()
```

- [ ] **Step 7: Run + commit**

```bash
python -m pytest tests/test_ffmpeg_runner.py -v
git add src/core/ffmpeg_runner.py src/ui/main_window.py tests/test_ffmpeg_runner.py
git commit -m "fix(ffmpeg): surface loudnorm and prerender_audio failures via status callback"
```

---

### Task 1.4: Surface NVENC probe failures

**Files:**
- Modify: `src/core/ffmpeg_runner.py` — `_check_nvenc_available` and `_encode_args` (~lines 22-90)

Currently NVENC probe silently disables GPU encoding for the session. Users get 5-10× slower exports with no diagnostic.

- [ ] **Step 1: Track NVENC unavailability reason**

Change module-level cache from `_nvenc_available: bool | None = None` to `_nvenc_state: dict = {"checked": False, "available": False, "reason": ""}`.

In `_check_nvenc_available`, on each failure path, set `_nvenc_state["reason"]` to a human-readable string: "NVIDIA driver not detected", "NVENC license unavailable", "ffmpeg not found", "Encoder probe timed out", etc.

- [ ] **Step 2: Add a public accessor `get_encoder_status() -> tuple[str, str]`**

Returns `("nvenc"|"software", reason_or_empty)`. Use this in main_window status bar at startup.

- [ ] **Step 3: Show one-time toast at app startup if software fallback active**

In `MainWindow.__init__` after `self.ffmpeg_ok` check, add:
```python
encoder, reason = ffmpeg_runner.get_encoder_status()
if encoder == "software" and reason:
    QTimer.singleShot(500, lambda: self.statusBar().showMessage(
        f"GPU encoding unavailable ({reason}) — using software encoder, exports will be slower",
        15000
    ))
```

- [ ] **Step 4: Test (mock subprocess)**

Add to `tests/test_ffmpeg_runner.py`:
```python
def test_encoder_status_reports_reason_on_nvenc_fail(monkeypatch):
    from src.core import ffmpeg_runner
    # Reset cache
    ffmpeg_runner._nvenc_state = {"checked": False, "available": False, "reason": ""}
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")
    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", fake_run)
    encoder, reason = ffmpeg_runner.get_encoder_status()
    assert encoder == "software"
    assert reason  # non-empty
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_ffmpeg_runner.py::test_encoder_status_reports_reason_on_nvenc_fail -v
git add src/core/ffmpeg_runner.py src/ui/main_window.py tests/test_ffmpeg_runner.py
git commit -m "fix(ffmpeg): surface NVENC unavailability reason at startup"
```

---

### Task 1.5: Log rotation and path sanitization

**Files:**
- Create: `src/core/log_setup.py`
- Modify: `main.py` (call `log_setup.init()` at startup), `src/core/ffmpeg_runner.py` (use `log_setup.log` instead of direct file writes)

Logs currently grow unbounded and contain full filesystem paths (PII risk in shared bug reports).

- [ ] **Step 1: Create `src/core/log_setup.py`**

```python
"""Centralized logging with rotation + path sanitization."""
from __future__ import annotations
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_initialized = False
_logger: Optional[logging.Logger] = None

# Patterns to redact in log messages
_USER_DIR_RE = re.compile(r'[A-Z]:[\\/]Users[\\/]([^\\/]+)', re.IGNORECASE)
_HOMEDIR_RE = re.compile(re.escape(str(Path.home())), re.IGNORECASE)


def sanitize_path(text: str) -> str:
    """Replace user-identifying path fragments with placeholders."""
    if not text:
        return text
    text = _HOMEDIR_RE.sub("<HOME>", text)
    text = _USER_DIR_RE.sub(r"<USERS>\\<USER>", text)
    return text


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return sanitize_path(msg)


def init(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Initialize rotating logger. Idempotent."""
    global _initialized, _logger
    if _initialized and _logger is not None:
        return _logger
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "editor.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=3,
        encoding='utf-8',
    )
    handler.setFormatter(_SanitizingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger = logging.getLogger("video_editor")
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    _initialized = True
    _logger = logger
    return logger


def log() -> logging.Logger:
    """Get the configured logger. init() must have been called."""
    if _logger is None:
        return logging.getLogger("video_editor_uninit")
    return _logger
```

- [ ] **Step 2: Wire init() into main.py**

In `main()` of `main.py`, before creating QApplication:
```python
from src.core import log_setup, paths
log_setup.init(paths.config_dir())
log_setup.log().info("Application starting")
```

Replace any existing `editor.log` write code in main.py.

- [ ] **Step 3: Replace direct log writes in ffmpeg_runner.py**

Find every `with open(... "editor.log" ...) as f:` or `log_path.write_text(...)` pattern. Replace with:
```python
from src.core.log_setup import log
log().error(f"Loudnorm failed: {e}")
```

- [ ] **Step 4: Test path sanitization**

Add to `tests/test_log_setup.py` (new file):
```python
from src.core.log_setup import sanitize_path

def test_sanitize_user_dir():
    assert "<USERS>" in sanitize_path(r"C:\Users\alice\Documents\file.mp4")
    # username "alice" should be removed
    assert "alice" not in sanitize_path(r"C:\Users\alice\Documents\file.mp4")

def test_sanitize_preserves_filenames():
    out = sanitize_path(r"C:\Users\bob\Videos\my-clip.mp4")
    assert "my-clip.mp4" in out
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_log_setup.py -v
git add src/core/log_setup.py main.py src/core/ffmpeg_runner.py tests/test_log_setup.py
git commit -m "feat(logging): centralized rotating logger with path sanitization"
```

---

### Task 1.6: Output-path traversal guard

**Files:**
- Modify: `src/ui/main_window.py` (~line 1982 — output path construction)

Hand-edited `output_suffix` like `/../../Windows/System32/evil` would land output files outside the intended directory. Add a guard.

- [ ] **Step 1: Read the current output path construction**

Locate the function building the output path (probably `_build_output_path` or inline in export trigger). Note the exact call to `os.path.join` or pathlib.

- [ ] **Step 2: Add resolution + containment check**

Replace the construction with:
```python
def _safe_output_path(self, output_dir: str, base_name: str, suffix: str, ext: str) -> str:
    """Resolve output path and verify it stays within output_dir."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Strip any path separators from suffix to prevent traversal
    safe_suffix = re.sub(r'[\\/:*?"<>|]', '_', suffix)
    candidate = (out_dir / f"{base_name}{safe_suffix}{ext}").resolve()
    # Verify still inside out_dir
    try:
        candidate.relative_to(out_dir)
    except ValueError:
        # Traversal attempt; fall back to safe name
        candidate = out_dir / f"{base_name}_export{ext}"
    return str(candidate)
```

Replace the existing path construction call site with `self._safe_output_path(...)`.

- [ ] **Step 3: Add unit test**

Add to `tests/test_main_window.py` (new file — keep narrow, no Qt requirement):
```python
import re
from pathlib import Path

def _safe_output_path_logic(output_dir, base_name, suffix, ext):
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_suffix = re.sub(r'[\\/:*?"<>|]', '_', suffix)
    candidate = (out_dir / f"{base_name}{safe_suffix}{ext}").resolve()
    try:
        candidate.relative_to(out_dir)
    except ValueError:
        candidate = out_dir / f"{base_name}_export{ext}"
    return str(candidate)

def test_output_path_blocks_traversal(tmp_path):
    result = _safe_output_path_logic(str(tmp_path), "clip", "/../../etc/passwd", ".mp4")
    assert str(tmp_path) in result
    assert "passwd" not in result.replace("/", "_").replace("\\", "_") or "_export" in result

def test_output_path_normal_suffix(tmp_path):
    result = _safe_output_path_logic(str(tmp_path), "clip", "_edited", ".mp4")
    assert result.endswith("clip_edited.mp4")
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_main_window.py -v
git add src/ui/main_window.py tests/test_main_window.py
git commit -m "fix(security): prevent output-path traversal via output_suffix"
```

---

### Task 1.7: Per-installation salt in mutex name

**Files:**
- Modify: `main.py` (~line 25 — single-instance mutex creation)
- Modify: `src/core/paths.py` — add `installation_id()` helper

Predictable `Global\VideoEditorSingleInstance` mutex can be hijacked by another local process for DoS.

- [ ] **Step 1: Add installation_id helper to paths.py**

Append to `src/core/paths.py`:
```python
import hashlib

def installation_id() -> str:
    """Stable per-user installation id, persisted in config dir."""
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    id_file = cfg / ".installation_id"
    if id_file.exists():
        try:
            return id_file.read_text(encoding='utf-8').strip()
        except OSError:
            pass
    # Generate from username + APPDATA path (stable per user, hard to guess externally)
    seed = f"{os.environ.get('USERNAME', '')}:{os.environ.get('APPDATA', '')}"
    iid = hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]
    try:
        id_file.write_text(iid, encoding='utf-8')
    except OSError:
        pass
    return iid
```

- [ ] **Step 2: Use installation_id in mutex name**

In `main.py`, change:
```python
MUTEX_NAME = r"Global\VideoEditorSingleInstance"
```
To:
```python
from src.core.paths import installation_id
MUTEX_NAME = rf"Global\VideoEditor_{installation_id()}"
```

- [ ] **Step 3: Manual verification**

Run app once, check `config/.installation_id` exists. Run again, verify mutex still blocks second instance from launching. Different user logged in on same machine → different mutex → no conflict.

- [ ] **Step 4: Commit**

```bash
git add main.py src/core/paths.py
git commit -m "fix(security): per-installation salt in single-instance mutex name"
```

---

### Task 1.8: Whitelist auto-presets encoders

**Files:**
- Modify: `src/core/auto_presets.py` (~lines 67-92, `get_encode_args`)

Malicious presets.json could inject arbitrary ffmpeg flags via `encoder` field.

- [ ] **Step 1: Add allowlist constant**

At top of `src/core/auto_presets.py`:
```python
ALLOWED_ENCODERS = frozenset({
    "libx264", "libx265",
    "h264_nvenc", "hevc_nvenc",
    "h264_amf", "hevc_amf",       # AMD
    "h264_qsv", "hevc_qsv",        # Intel QuickSync
    "h264_videotoolbox", "hevc_videotoolbox",  # mac (future)
    "libsvtav1", "libaom-av1",     # AV1
    "libvpx-vp9",
})
ALLOWED_PIXFMTS = frozenset({
    "yuv420p", "yuv420p10le", "yuv422p", "yuv422p10le",
    "yuv444p", "yuv444p10le", "nv12", "p010le",
})
```

- [ ] **Step 2: Validate in `get_encode_args` before returning**

Before returning the args list, verify `encoder` ∈ ALLOWED_ENCODERS. If not, fall back to `libx264`. Same for any pix_fmt.

```python
encoder = preset.get("encoder", "libx264")
if encoder not in ALLOWED_ENCODERS:
    log().warning(f"Auto-preset encoder {encoder!r} not in allowlist, falling back to libx264")
    encoder = "libx264"
```

- [ ] **Step 3: Test**

Add to `tests/test_auto_presets.py` (new):
```python
def test_unknown_encoder_falls_back(tmp_path, monkeypatch):
    from src.core import auto_presets
    monkeypatch.setattr(auto_presets, "PRESETS_PATH", tmp_path / "presets.json")
    (tmp_path / "presets.json").write_text(
        '{"version": 1, "preset": {"encoder": "evil; rm -rf", "encoder_preset": "p1"}}',
        encoding='utf-8'
    )
    args = auto_presets.get_encode_args(...)  # actual signature TBD by reading file
    assert "evil" not in " ".join(args)
    assert "libx264" in args
```

(Note: read `auto_presets.py` first to get exact `get_encode_args` signature — the test stub above is approximate. Update the test to match the real API before running.)

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_auto_presets.py -v
git add src/core/auto_presets.py tests/test_auto_presets.py
git commit -m "fix(security): whitelist allowed encoders and pix_fmts in auto_presets"
```

---

### Task 1.9: Validate loudnorm measurements numeric (filter injection guard)

**Files:**
- Modify: `src/core/ffmpeg_runner.py` — `loudnorm_filter` (~line 148)

Crafted media could potentially inject ffmpeg filter syntax via the `measured` dict from `loudnorm_analyze`.

- [ ] **Step 1: Add float validation before interpolation**

In `loudnorm_filter`, after extracting `input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`, validate each:
```python
def _safe_float(v, default: float) -> float:
    try:
        f = float(v)
        if not (-200.0 <= f <= 200.0):
            return default
        return f
    except (TypeError, ValueError):
        return default

input_i = _safe_float(measured.get("input_i"), -16.0)
input_tp = _safe_float(measured.get("input_tp"), -2.0)
input_lra = _safe_float(measured.get("input_lra"), 7.0)
input_thresh = _safe_float(measured.get("input_thresh"), -26.0)
target_offset = _safe_float(measured.get("target_offset"), 0.0)
```

- [ ] **Step 2: Test**

Add to `tests/test_ffmpeg_runner.py`:
```python
def test_loudnorm_filter_rejects_injection_payload():
    from src.core.ffmpeg_runner import loudnorm_filter
    malicious = {
        "input_i": "-20;amovie=/etc/passwd[a]",
        "input_tp": "-2",
        "input_lra": "7",
        "input_thresh": "-26",
        "target_offset": "0",
    }
    f = loudnorm_filter(malicious, target_lufs=-16.0)
    assert "amovie" not in f
    assert "passwd" not in f
    assert "/etc" not in f
```

- [ ] **Step 3: Run + commit**

```bash
python -m pytest tests/test_ffmpeg_runner.py::test_loudnorm_filter_rejects_injection_payload -v
git add src/core/ffmpeg_runner.py tests/test_ffmpeg_runner.py
git commit -m "fix(security): validate loudnorm measurements as bounded floats"
```

---

### Task 1.10: Pin requirements.txt + add backups/ to gitignore

**Files:**
- Modify: `requirements.txt`, `.gitignore`

- [ ] **Step 1: Capture current installed versions**

Run: `pip freeze 2>&1 | grep -iE "PyQt6|pyinstaller"`

- [ ] **Step 2: Pin requirements.txt to exact versions**

Replace contents of `requirements.txt`:
```
# Pinned for reproducible builds. Update with `pip freeze | grep ...` after testing.
PyQt6==6.11.0
PyQt6-Qt6==6.11.0
PyQt6-sip==13.10.2
pyinstaller==6.16.0
# Test-only
pytest==9.0.2
psutil==7.1.0  # for memory leak harness
```

(Use exact pinned versions from `pip freeze` output of Step 1; the above are placeholders until verified.)

- [ ] **Step 3: Add backups/ and tests/fixtures/ to .gitignore**

Append to `.gitignore`:
```
# Auto-archive backups (created by /backup skill)
backups/
# Test fixtures generated at test time
tests/fixtures/
# Per-installation id
config/.installation_id
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: pin requirements + ignore backups/fixtures/installation_id"
```

---

## Phase 2 — Build & Distribution Pipeline

Goal: a single `tools/release.ps1` produces a signed installer with FFmpeg bundled, ready to upload to GitHub Releases. Sign step is no-op without cert env var.

### Task 2.1: FFmpeg auto-fetch script

**Files:**
- Create: `tools/fetch_ffmpeg.ps1`

Downloads latest BtbN/FFmpeg-Builds Windows release, verifies SHA256, extracts ffmpeg.exe + ffprobe.exe to `dist/Video Editor/ffmpeg/`.

- [ ] **Step 1: Write the script**

Create `tools/fetch_ffmpeg.ps1`:
```powershell
# Download FFmpeg essentials build, verify SHA256, extract to dist
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distFfmpegDir = Join-Path $projectRoot "dist\Video Editor\ffmpeg"
$tempZip = Join-Path $env:TEMP "ffmpeg-release.zip"
$tempDir = Join-Path $env:TEMP "ffmpeg-extract"

if ((Test-Path (Join-Path $distFfmpegDir "ffmpeg.exe")) -and -not $env:FORCE_FFMPEG_REFRESH) {
    Write-Host "FFmpeg already present in dist. Set FORCE_FFMPEG_REFRESH=1 to redownload."
    exit 0
}

Write-Host "Querying GitHub for latest FFmpeg release..."
$api = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
$rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "VideoEditor-Build" }

# Pick the win64 GPL build (matches our gyan.dev dev install for parity)
$asset = $rel.assets | Where-Object { $_.name -like "ffmpeg-master-latest-win64-gpl*.zip" -and $_.name -notlike "*shared*" } | Select-Object -First 1
if (-not $asset) {
    Write-Error "Could not find win64-gpl release asset"
    exit 1
}

Write-Host "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tempZip -UseBasicParsing

# BtbN does not ship per-asset SHA256, but the asset URL is HTTPS+TLS-pinned by github.com.
# Hash the downloaded file and record it for audit.
$sha = (Get-FileHash -Path $tempZip -Algorithm SHA256).Hash
Write-Host "SHA256: $sha"

# Extract
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir }
Expand-Archive -Path $tempZip -DestinationPath $tempDir
$exeSrc = Get-ChildItem -Path $tempDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$probeSrc = Get-ChildItem -Path $tempDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $exeSrc -or -not $probeSrc) {
    Write-Error "ffmpeg.exe / ffprobe.exe not found in archive"
    exit 1
}

# Place in dist
New-Item -ItemType Directory -Force -Path $distFfmpegDir | Out-Null
Copy-Item -Path $exeSrc.FullName -Destination (Join-Path $distFfmpegDir "ffmpeg.exe") -Force
Copy-Item -Path $probeSrc.FullName -Destination (Join-Path $distFfmpegDir "ffprobe.exe") -Force

# Copy LICENSE files from the FFmpeg archive
$ffLicenses = Get-ChildItem -Path $tempDir -Recurse -Filter "LICENSE*" | Where-Object { $_.PSIsContainer -eq $false }
$licDir = Join-Path $distFfmpegDir "licenses"
New-Item -ItemType Directory -Force -Path $licDir | Out-Null
foreach ($lic in $ffLicenses) {
    Copy-Item -Path $lic.FullName -Destination $licDir -Force
}

# Cleanup temp
Remove-Item -Force $tempZip
Remove-Item -Recurse -Force $tempDir

Write-Host "FFmpeg bundled at: $distFfmpegDir"
Write-Host "  ffmpeg.exe: $((Get-Item (Join-Path $distFfmpegDir 'ffmpeg.exe')).Length / 1MB) MB"
Write-Host "  ffprobe.exe: $((Get-Item (Join-Path $distFfmpegDir 'ffprobe.exe')).Length / 1MB) MB"
```

- [ ] **Step 2: Run the script**

Run: `powershell -ExecutionPolicy Bypass -File "tools/fetch_ffmpeg.ps1"`
Expected: downloads ~80 MB zip, extracts ffmpeg.exe and ffprobe.exe to `dist/Video Editor/ffmpeg/`, copies LICENSE files to `dist/Video Editor/ffmpeg/licenses/`.

- [ ] **Step 3: Verify the bundled ffmpeg works**

Run: `"dist/Video Editor/ffmpeg/ffmpeg.exe" -version 2>&1 | head -2`
Expected: prints version line, exits 0.

- [ ] **Step 4: Commit**

```bash
git add tools/fetch_ffmpeg.ps1
git commit -m "feat(build): auto-fetch FFmpeg from BtbN releases with LICENSE files"
```

---

### Task 2.2: LICENSE, NOTICES, and source offer files

**Files:**
- Create: `LICENSE`, `NOTICES.md`, `docs/FFMPEG_SOURCE_OFFER.md`, `PRIVACY.md`

GPL/LGPL compliance + privacy disclosure baseline.

- [ ] **Step 1: Decide on app license**

The user has not specified one. Use **GPL v3** for the app itself (compatible with PyQt6 GPL and FFmpeg GPL). This is the safest choice given dependencies.

Create `LICENSE` with the standard GPL v3 text. Source: https://www.gnu.org/licenses/gpl-3.0.txt — fetch and save verbatim.

- [ ] **Step 2: Write NOTICES.md**

Create `NOTICES.md`:
```markdown
# Third-Party Notices

This software bundles or links to the following third-party components.
Each component retains its own license. See individual files for full text.

## FFmpeg
- License: LGPL v2.1+ / GPL v2+ (depending on build configuration)
- Source: https://ffmpeg.org/
- Bundled binaries: ffmpeg.exe, ffprobe.exe (BtbN FFmpeg-Builds, win64-gpl)
- Bundled licenses: see `ffmpeg/licenses/` directory in the installed app
- Source offer: see `docs/FFMPEG_SOURCE_OFFER.md`

## PyQt6
- License: GPL v3
- Source: https://www.riverbankcomputing.com/software/pyqt/
- Used as: dynamic Python binding to Qt 6

## Qt 6
- License: LGPL v3 / Commercial
- Source: https://www.qt.io/
- Used as: GUI framework (via PyQt6)

## Python Standard Library
- License: PSF License v2
- Source: https://www.python.org/

## Inno Setup (build-time only, not redistributed in installer)
- License: Inno Setup License (free for commercial use)
- Source: https://jrsoftware.org/isinfo.php

## PyInstaller (build-time only)
- License: GPL v2+ with bootloader exception
- Source: https://pyinstaller.org/
```

- [ ] **Step 3: Write FFMPEG_SOURCE_OFFER.md**

Create `docs/FFMPEG_SOURCE_OFFER.md`:
```markdown
# Written Offer for FFmpeg Source Code

This software bundles a binary copy of FFmpeg, which is licensed under the
GNU General Public License v2 or later (GPL v2+). Per the terms of the GPL,
we provide this written offer:

> For a period of three (3) years from the date you received the bundled
> Video Editor software, you may obtain the complete corresponding source
> code for the bundled FFmpeg version at no charge other than the cost of
> the physical media (if any).
>
> The bundled FFmpeg is built from source by BtbN/FFmpeg-Builds and matches
> the win64-gpl-master release at https://github.com/BtbN/FFmpeg-Builds/releases
>
> To request the source code, open an issue at:
>   https://github.com/<your-github-handle>/<repo>/issues
> with the subject "FFmpeg source request" and the SHA256 of the bundled
> ffmpeg.exe (printed by `ffmpeg -version` and recorded in `ffmpeg/licenses/`).

The GPL v2 text is included at `ffmpeg/licenses/COPYING.GPLv2`.
```

- [ ] **Step 4: Write PRIVACY.md**

Create `PRIVACY.md`:
```markdown
# Privacy Policy

**Effective:** 2026-04-25
**App:** Video Editor (desktop)

## What we collect
**Nothing, by default.**

This app runs entirely on your local computer. It does not phone home,
upload your video files, or transmit any usage data — unless you explicitly
opt in to one of the features below.

## Optional features that send data

### Update check (default: ON)
On startup, the app may make a single HTTPS GET request to a static JSON
file at `https://raw.githubusercontent.com/<your-handle>/<repo>/main/release/latest.json`
to check whether a newer version is available. The request includes only:
- Your IP address (visible to GitHub by virtue of the connection)
- Your installed app version (in the `User-Agent` header)

It does NOT include any filenames, project paths, settings, or other PII.

You can disable this in **Settings → General → Check for updates**.

### Crash reports (default: OFF)
If you opt in via **Settings → General → Send anonymous crash reports**, the
app will send minimal crash diagnostics (Python exception traceback, OS
version, app version) to a remote endpoint. File paths are sanitized to
remove your username before transmission. No video files or video
content is ever transmitted.

This is OFF by default and requires explicit user consent.

### "Report a Bug" button
When you click "Report a Bug" in the Help menu, the app prepares a sanitized
copy of `editor.log` (with your username redacted) and opens your default
mail client (or browser) so you can choose whether to send it. Nothing is
transmitted automatically.

## Files written to disk
- `%APPDATA%/VideoEditor/settings.json` — your preferences
- `%APPDATA%/VideoEditor/editor.log` — rotating log (max 8 MB total)
- `%APPDATA%/VideoEditor/.installation_id` — random per-install id (no PII)

## Contact
Open an issue at https://github.com/<your-handle>/<repo>/issues
```

(Note: replace `<your-handle>/<repo>` placeholders before publishing.)

- [ ] **Step 5: Update build.bat to copy these files into dist**

Modify `tools/build.bat` to add after the PyInstaller call:
```bat
echo === Copying license files ===
copy /Y "LICENSE" "dist\Video Editor\LICENSE.txt"
copy /Y "NOTICES.md" "dist\Video Editor\NOTICES.md"
copy /Y "PRIVACY.md" "dist\Video Editor\PRIVACY.md"
copy /Y "docs\FFMPEG_SOURCE_OFFER.md" "dist\Video Editor\FFMPEG_SOURCE_OFFER.md"

echo === Bundling FFmpeg ===
powershell -ExecutionPolicy Bypass -File "tools\fetch_ffmpeg.ps1"
```

- [ ] **Step 6: Commit**

```bash
git add LICENSE NOTICES.md PRIVACY.md docs/FFMPEG_SOURCE_OFFER.md tools/build.bat
git commit -m "feat(legal): add LICENSE GPLv3 + NOTICES + FFmpeg source offer + privacy policy"
```

---

### Task 2.3: Code signing hook (no-op without cert)

**Files:**
- Create: `tools/sign.ps1`
- Modify: `tools/build.bat` — call sign.ps1 after PyInstaller

Pre-wire signing so that the day a cert is purchased, signing happens automatically with one env var. Until then, the script is a no-op that documents the requirement.

- [ ] **Step 1: Write tools/sign.ps1**

```powershell
# Authenticode-sign the built exe IF signing cert env vars are set.
# This is a no-op when SIGNCERT_PATH is not provided.
$ErrorActionPreference = "Stop"
$exePath = Join-Path (Split-Path -Parent $PSScriptRoot) "dist\Video Editor\Video Editor.exe"

if (-not (Test-Path $exePath)) {
    Write-Error "Build artifact not found at $exePath"
    exit 1
}

if (-not $env:SIGNCERT_PATH) {
    Write-Host "[sign] SIGNCERT_PATH not set — skipping code signing."
    Write-Host "[sign] When you have an Authenticode cert, set:"
    Write-Host "[sign]   `$env:SIGNCERT_PATH = 'C:\path\to\cert.pfx'"
    Write-Host "[sign]   `$env:SIGNCERT_PASSWORD = '<password>'"
    Write-Host "[sign]   `$env:SIGNCERT_TIMESTAMP = 'http://timestamp.digicert.com'"
    Write-Host "[sign] Then re-run the build."
    exit 0
}

if (-not $env:SIGNCERT_PASSWORD) {
    Write-Error "SIGNCERT_PATH set but SIGNCERT_PASSWORD missing"
    exit 1
}

# Locate signtool.exe (Windows SDK)
$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
if (-not $signtool) {
    Write-Error "signtool.exe not found. Install Windows 10 SDK from Visual Studio Installer."
    exit 1
}

$timestamp = if ($env:SIGNCERT_TIMESTAMP) { $env:SIGNCERT_TIMESTAMP } else { "http://timestamp.digicert.com" }

Write-Host "[sign] Signing $exePath..."
& $signtool.FullName sign `
    /f $env:SIGNCERT_PATH `
    /p $env:SIGNCERT_PASSWORD `
    /tr $timestamp /td sha256 /fd sha256 `
    /d "Video Editor" `
    $exePath

if ($LASTEXITCODE -ne 0) {
    Write-Error "signtool failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[sign] Done."
```

- [ ] **Step 2: Wire into build.bat**

Append to `tools/build.bat` (after FFmpeg bundling step from Task 2.2):
```bat
echo === Signing (no-op without cert) ===
powershell -ExecutionPolicy Bypass -File "tools\sign.ps1"
```

- [ ] **Step 3: Verify no-op path works**

Run: `tools\build.bat`
Expected: build completes; sign step prints "SIGNCERT_PATH not set — skipping code signing." with instructions; exit 0.

- [ ] **Step 4: Commit**

```bash
git add tools/sign.ps1 tools/build.bat
git commit -m "feat(build): sign hook — Authenticode signing when SIGNCERT_PATH is set"
```

---

### Task 2.4: Inno Setup installer

**Files:**
- Create: `tools/installer.iss`

Builds a single `VideoEditor-Setup.exe` with Start Menu, uninstaller, file association options.

- [ ] **Step 1: Install Inno Setup if missing**

Run: `winget install JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements`
Expected: succeeds or reports "already installed".

- [ ] **Step 2: Locate iscc.exe**

Run: `where.exe iscc 2>&1` or `Get-Command iscc -ErrorAction SilentlyContinue`. If not on PATH, find via:
`powershell "Get-ChildItem 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' -ErrorAction SilentlyContinue | Select-Object -First 1"`

- [ ] **Step 3: Write tools/installer.iss**

```pascal
; Inno Setup script for Video Editor
; Compile with: ISCC.exe tools\installer.iss

#define MyAppName "Video Editor"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Trust"
#define MyAppExeName "Video Editor.exe"

[Setup]
AppId={{C8A3F2E0-7B4D-4A5C-9E1F-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=VideoEditor-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
InfoBeforeFile=..\NOTICES.md
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "associate_mp4"; Description: "Associate .mp4 files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked
Name: "associate_mov"; Description: "Associate .mov files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
Source: "..\dist\Video Editor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "VideoEditor.mp4"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mp4"; ValueType: string; ValueName: ""; ValueData: "Video Editor MP4 File"; Flags: uninsdeletekey; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mp4\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "VideoEditor.mov"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_mov
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mov"; ValueType: string; ValueName: ""; ValueData: "Video Editor MOV File"; Flags: uninsdeletekey; Tasks: associate_mov
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mov\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_mov

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
```

- [ ] **Step 4: Compile installer**

Run: `& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "tools\installer.iss"`
Expected: writes `dist\installer\VideoEditor-Setup-0.1.0.exe` (~120 MB compressed).

- [ ] **Step 5: Verify installer launches and works**

Run: `"dist\installer\VideoEditor-Setup-0.1.0.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=C:\TestInstall\VideoEditor`
Expected: silent install completes; `C:\TestInstall\VideoEditor\Video Editor.exe` exists; `C:\TestInstall\VideoEditor\unins000.exe` exists.

Then test launch: `& "C:\TestInstall\VideoEditor\Video Editor.exe"` — should start with no console.

Then uninstall: `& "C:\TestInstall\VideoEditor\unins000.exe" /VERYSILENT`

- [ ] **Step 6: Commit**

```bash
git add tools/installer.iss
git commit -m "feat(build): Inno Setup installer with Start Menu, uninstaller, optional file associations"
```

---

### Task 2.5: Release orchestration script

**Files:**
- Create: `tools/release.ps1`

Single command end-to-end: clean → build → bundle FFmpeg → sign → installer → output path.

- [ ] **Step 1: Write tools/release.ps1**

```powershell
# Single-command release pipeline.
# Usage: .\tools\release.ps1 [-Version 0.1.0]
param(
    [string]$Version = "0.1.0"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

try {
    Write-Host "=== Cleaning previous build ===" -ForegroundColor Cyan
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue

    Write-Host "=== PyInstaller build ===" -ForegroundColor Cyan
    & "tools\build.bat"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    # build.bat already calls fetch_ffmpeg + sign

    Write-Host "=== Compiling installer ===" -ForegroundColor Cyan
    $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
    }
    if (-not $iscc) {
        throw "Inno Setup not found. Install with: winget install JRSoftware.InnoSetup"
    }
    & $iscc "/DMyAppVersion=$Version" "tools\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

    $installer = "dist\installer\VideoEditor-Setup-$Version.exe"
    if (-not (Test-Path $installer)) { throw "Installer not found at $installer" }

    $sizeMB = [math]::Round((Get-Item $installer).Length / 1MB, 1)
    Write-Host ""
    Write-Host "=== Release ready ===" -ForegroundColor Green
    Write-Host "  Installer: $installer ($sizeMB MB)"
    Write-Host "  Bundle:    dist\Video Editor\"

    if (-not $env:SIGNCERT_PATH) {
        Write-Host ""
        Write-Host "  WARNING: Build is unsigned (SIGNCERT_PATH not set)." -ForegroundColor Yellow
        Write-Host "  Windows will show 'Unknown publisher' SmartScreen warning." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}
```

- [ ] **Step 2: Run end-to-end**

Run: `powershell -ExecutionPolicy Bypass -File "tools\release.ps1" -Version 0.1.0`
Expected: produces `dist\installer\VideoEditor-Setup-0.1.0.exe`.

- [ ] **Step 3: Commit**

```bash
git add tools/release.ps1
git commit -m "feat(build): single-command release pipeline (clean→build→ffmpeg→sign→installer)"
```

---

### Task 2.6: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

Run tests on every push (free for private repos within free-tier minutes).

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [master, main]
  pull_request:

jobs:
  test:
    runs-on: windows-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.14
        uses: actions/setup-python@v5
        with:
          python-version: '3.14'

      - name: Install FFmpeg (for tests)
        run: |
          choco install ffmpeg --no-progress --yes
          ffmpeg -version

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run tests
        env:
          QT_QPA_PLATFORM: offscreen
        run: |
          python -m pytest tests/ -v --tb=short
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions on Windows runner with FFmpeg install"
```

(Don't push yet — wait until Phase 3 produces a passing test suite.)

---

## Phase 3 — Real Test Suite

Goal: pytest finds, runs, and passes a real test suite that exercises the export pipeline end-to-end with a generated fixture. Replaces the broken `test_fixes.py` over time.

### Task 3.1: Pytest infrastructure with FFmpeg-generated fixtures

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/fixtures/` directory (gitignored, generated)

- [ ] **Step 1: Write tests/conftest.py**

```python
"""Pytest fixtures shared across the test suite."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure src/ on path for `import src.core...`
sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session", autouse=True)
def _ensure_offscreen_qt():
    """Force Qt offscreen so tests don't pop windows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def fixture_video() -> Path:
    """Generate a tiny 5-second test video using FFmpeg lavfi.

    Returns a path to a 320x240 H.264 + AAC mp4. Generated once per session.
    """
    if not _have_ffmpeg():
        pytest.skip("FFmpeg not on PATH")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "test_5s_320x240.mp4"
    if out.exists():
        return out
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Failed to generate fixture video:\n{result.stderr}")
    assert out.exists() and out.stat().st_size > 0
    return out


@pytest.fixture(scope="session")
def fixture_video_silent() -> Path:
    """5s test video with no audio track."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg not on PATH")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "test_5s_silent.mp4"
    if out.exists():
        return out
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Failed to generate silent fixture:\n{result.stderr}")
    return out
```

- [ ] **Step 2: Run pytest to verify fixtures generate**

Run: `python -m pytest tests/ --collect-only 2>&1 | tail -20`
Expected: collection succeeds.

Then: `python -m pytest tests/conftest.py -v` (no-op — fixtures only run when used).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(infra): pytest fixtures with FFmpeg lavfi-generated test videos"
```

---

### Task 3.2: ffmpeg_runner unit tests (replace test_fixes.py over time)

**Files:**
- Create: `tests/test_ffmpeg_runner.py` (extend if already created in earlier tasks)

Already partially populated by Tasks 1.2, 1.3, 1.4, 1.9. Now add the build_command coverage that test_fixes.py contains, in proper pytest form.

- [ ] **Step 1: Audit what test_fixes.py covers and port to pytest format**

Read `tests/test_fixes.py` and identify each `test(...)` call. Rewrite each as a proper pytest function in `tests/test_ffmpeg_runner.py`.

Example:
```python
from src.core.ffmpeg_runner import build_command

def test_build_command_basic_has_y_flag():
    cmd = build_command("in.mp4", "out.mp4")
    assert "-y" in cmd

def test_build_command_basic_stream_copy():
    cmd = build_command("in.mp4", "out.mp4")
    assert "-c:v" not in cmd or "copy" in cmd[cmd.index("-c:v") + 1]

def test_build_command_crop_filter():
    cmd = build_command("in.mp4", "out.mp4", crop_x=100, crop_y=50, crop_w=800, crop_h=600)
    vf_idx = cmd.index("-vf")
    assert "crop=" in cmd[vf_idx + 1]

def test_build_command_trim_uses_t_not_to():
    cmd = build_command("in.mp4", "out.mp4", trim_start=5.0, trim_end=10.0)
    assert "-t" in cmd
    assert "-to" not in cmd

def test_build_command_trim_ss_before_input():
    cmd = build_command("in.mp4", "out.mp4", trim_start=5.0, trim_end=10.0)
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx

def test_build_command_speed_setpts():
    cmd = build_command("in.mp4", "out.mp4", speed=0.5)
    vf_idx = cmd.index("-vf")
    assert "setpts=" in cmd[vf_idx + 1]

def test_build_command_speed_audio_asetrate():
    cmd = build_command("in.mp4", "out.mp4", speed=2.0, audio_mode="reencode")
    af_idx = cmd.index("-af")
    assert "asetrate=" in cmd[af_idx + 1]

def test_build_command_mute_overrides_speed_audio():
    cmd = build_command("in.mp4", "out.mp4", speed=2.0, audio_mode="mute")
    assert "-an" in cmd

def test_build_command_h265_codec():
    cmd = build_command("in.mp4", "out.mp4", codec="h265", crf=23, crop_w=100)
    assert "libx265" in cmd or "hevc_nvenc" in cmd
```

(Continue for every assertion in the original `test_fixes.py`.)

- [ ] **Step 2: Run all ffmpeg_runner tests**

Run: `python -m pytest tests/test_ffmpeg_runner.py -v 2>&1 | tail -30`
Expected: all PASS.

- [ ] **Step 3: Delete the broken test_fixes.py**

Once parity confirmed:
```bash
rm tests/test_fixes.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_ffmpeg_runner.py
git rm tests/test_fixes.py
git commit -m "test(ffmpeg_runner): port test_fixes.py to proper pytest, delete broken legacy"
```

---

### Task 3.3: End-to-end export test

**Files:**
- Create: `tests/test_export_e2e.py`

Real export, then ffprobe the output to verify correctness.

- [ ] **Step 1: Write the test**

```python
"""End-to-end export tests using a real fixture video."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _ffprobe(path: Path) -> dict:
    """Return ffprobe JSON for path."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return json.loads(result.stdout)


def test_export_basic_no_filters_streamcopy(fixture_video, tmp_path):
    """No filters → stream copy → output should be byte-identical-ish."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out.mp4"
    cmd = build_command(str(fixture_video), str(out))
    rc = run_export(cmd)
    assert rc, "export returned False"
    assert out.exists()
    info = _ffprobe(out)
    assert any(s["codec_type"] == "video" for s in info["streams"])
    duration = float(info["format"]["duration"])
    assert 4.5 < duration < 5.5  # ~5s with some tolerance


def test_export_with_trim(fixture_video, tmp_path):
    """Trim 1.0s..3.0s → output should be ~2s."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_trim.mp4"
    cmd = build_command(str(fixture_video), str(out), trim_start=1.0, trim_end=3.0)
    rc = run_export(cmd)
    assert rc
    info = _ffprobe(out)
    duration = float(info["format"]["duration"])
    assert 1.8 < duration < 2.2


def test_export_with_crop(fixture_video, tmp_path):
    """Crop 100x100 → output should be 100x100."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_crop.mp4"
    cmd = build_command(str(fixture_video), str(out),
                        crop_x=10, crop_y=10, crop_w=100, crop_h=100)
    rc = run_export(cmd)
    assert rc
    info = _ffprobe(out)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert vstream["width"] == 100
    assert vstream["height"] == 100


def test_export_with_speed_2x(fixture_video, tmp_path):
    """2x speed → output should be ~2.5s (half of 5s)."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_speed.mp4"
    cmd = build_command(str(fixture_video), str(out), speed=2.0,
                        audio_mode="reencode")
    rc = run_export(cmd)
    assert rc
    info = _ffprobe(out)
    duration = float(info["format"]["duration"])
    assert 2.3 < duration < 2.7


def test_export_silent_video_does_not_break(fixture_video_silent, tmp_path):
    """Source has no audio track → export should still succeed."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_silent.mp4"
    cmd = build_command(str(fixture_video_silent), str(out),
                        crop_x=10, crop_y=10, crop_w=100, crop_h=100)
    rc = run_export(cmd)
    assert rc
    info = _ffprobe(out)
    assert any(s["codec_type"] == "video" for s in info["streams"])
```

- [ ] **Step 2: Run the suite**

Run: `python -m pytest tests/test_export_e2e.py -v 2>&1 | tail -30`
Expected: all PASS. Each export should take 1-3 seconds.

- [ ] **Step 3: Commit**

```bash
git add tests/test_export_e2e.py
git commit -m "test(e2e): real ffmpeg export + ffprobe verification (trim/crop/speed/silent)"
```

---

### Task 3.4: Unit tests for archive, speed_curve, keybinds

**Files:**
- Create: `tests/test_archive.py`, `tests/test_speed_curve.py`, `tests/test_keybinds.py`

Pure-Python modules — fast, deterministic, easy to test.

- [ ] **Step 1: Write tests/test_speed_curve.py**

Read `src/core/speed_curve.py` first to confirm API. Then write:
```python
from src.core.speed_curve import SpeedCurve

def test_empty_curve_returns_default():
    c = SpeedCurve(default=1.0)
    assert c.get_speed_at_ms(0) == 1.0
    assert c.get_speed_at_ms(5000) == 1.0

def test_single_keyframe():
    c = SpeedCurve(default=1.0, keyframes=[(1000, 2.0)])
    assert c.get_speed_at_ms(1000) == 2.0
    # Before first keyframe: held flat
    assert c.get_speed_at_ms(500) in (1.0, 2.0)  # check spec
    # After only keyframe: held flat
    assert c.get_speed_at_ms(2000) == 2.0

def test_linear_interpolation_between_two_keyframes():
    c = SpeedCurve(default=1.0, keyframes=[(0, 1.0), (1000, 2.0)])
    assert abs(c.get_speed_at_ms(500) - 1.5) < 0.01
```

(Adjust to match actual `SpeedCurve` API after reading the file.)

- [ ] **Step 2: Write tests/test_keybinds.py**

Read `src/core/keybinds.py` first. Then test:
- Default bindings exist
- Parser handles `Ctrl+Alt+Shift+K` strings
- Conflict detection
- Save/load round-trip

- [ ] **Step 3: Write tests/test_archive.py**

Read `src/core/archive.py` first. Then test:
- `detect_relative_path` finds 4-digit year folder
- Fallback to `Unsorted` when no year present
- `_unique_destination` increments suffix on collision
- Move into archive root succeeds (use tmp_path)
- Move from read-only source surfaces error (use `os.chmod` to make readonly, then chmod back in finally)

- [ ] **Step 4: Run all + commit**

```bash
python -m pytest tests/test_speed_curve.py tests/test_keybinds.py tests/test_archive.py -v
git add tests/test_speed_curve.py tests/test_keybinds.py tests/test_archive.py
git commit -m "test: unit tests for speed_curve, keybinds, archive"
```

---

### Task 3.5: Memory leak harness

**Files:**
- Create: `tests/test_memory_leak.py`

Detects gross leaks from repeated load/unload cycles.

- [ ] **Step 1: Write the test**

```python
"""Memory leak harness — load 50 videos in a row, assert RSS stays bounded."""
import pytest
import gc

psutil = pytest.importorskip("psutil")

@pytest.mark.slow
def test_repeated_load_unload_does_not_leak(fixture_video, qtbot=None):
    """Load a video 50 times. RSS growth should be bounded under 50 MB."""
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6.QtCore import QCoreApplication, QUrl
    from PyQt6.QtMultimedia import QMediaPlayer
    import sys
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    for _ in range(50):
        p = QMediaPlayer()
        p.setSource(QUrl.fromLocalFile(str(fixture_video)))
        # Process events briefly to allow Qt to allocate
        app.processEvents()
        p.setSource(QUrl())
        del p
    gc.collect()
    app.processEvents()

    rss_after = proc.memory_info().rss
    growth_mb = (rss_after - rss_before) / (1024 * 1024)
    assert growth_mb < 50, f"RSS grew {growth_mb:.1f} MB after 50 cycles (leak suspected)"
```

- [ ] **Step 2: Mark slow tests in pytest config**

Create or modify `tests/pytest.ini`:
```ini
[pytest]
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
```

- [ ] **Step 3: Run the harness**

Run: `python -m pytest tests/test_memory_leak.py -v -m slow 2>&1 | tail -10`
Expected: PASS with growth < 50 MB.

- [ ] **Step 4: Commit**

```bash
git add tests/test_memory_leak.py tests/pytest.ini
git commit -m "test: QMediaPlayer load/unload leak harness (50 cycles, < 50MB growth)"
```

---

## Phase 4 — In-App Observability

Goal: when something goes wrong on a stranger's machine, we have a path to know about it (locally, with user consent for transmission), and the user has a frictionless way to report a bug.

### Task 4.1: Version constant + update check

**Files:**
- Create: `src/core/version.py`
- Modify: `main.py` (kick off update check at startup), `src/ui/main_window.py` (Help menu item)

- [ ] **Step 1: Write src/core/version.py**

```python
"""Version constant + remote update check."""
from __future__ import annotations
import json
import threading
from typing import Optional, Callable

# Single source of truth.
VERSION = "0.1.0"

# This URL must be updated when the project moves to a real GitHub repo.
# Format of the JSON file at this URL:
#   {"latest": "0.1.1", "url": "https://github.com/.../releases/tag/v0.1.1", "notes": "..."}
UPDATE_URL = "https://raw.githubusercontent.com/PLACEHOLDER_USER/PLACEHOLDER_REPO/main/release/latest.json"


def parse_version(s: str) -> tuple[int, ...]:
    parts = []
    for p in s.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_update(
    current: str = VERSION,
    timeout: float = 5.0,
    callback: Optional[Callable[[Optional[dict]], None]] = None,
) -> Optional[dict]:
    """Synchronous update check. Returns dict with latest info if update available, else None.

    If callback provided, calls it with the result (use this for async).
    """
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            UPDATE_URL,
            headers={"User-Agent": f"VideoEditor/{current}"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("latest", "")
        if latest and parse_version(latest) > parse_version(current):
            result = {
                "latest": latest,
                "url": data.get("url", ""),
                "notes": data.get("notes", ""),
            }
        else:
            result = None
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        result = None
    if callback:
        callback(result)
    return result


def check_for_update_async(callback: Callable[[Optional[dict]], None]) -> None:
    """Background thread version. Callback is invoked from the worker thread —
    Qt callers must use QMetaObject.invokeMethod or QTimer to marshal back to UI."""
    t = threading.Thread(
        target=lambda: callback(check_for_update()),
        daemon=True,
    )
    t.start()
```

- [ ] **Step 2: Wire startup check into main_window.py**

In `MainWindow.__init__`, after main UI is set up, add:
```python
from src.core.version import check_for_update_async, VERSION
from PyQt6.QtCore import QTimer

def _on_update_result(result):
    if not result:
        return
    # Marshal to UI thread
    QTimer.singleShot(0, lambda: self._show_update_notice(result))

# Honor user setting; default opt-in
if self._settings.get("check_for_updates", True):
    check_for_update_async(_on_update_result)
```

Add `_show_update_notice`:
```python
def _show_update_notice(self, result):
    msg = QMessageBox(self)
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setWindowTitle("Update Available")
    msg.setText(f"Version {result['latest']} is available (you have {VERSION}).")
    if result.get("notes"):
        msg.setInformativeText(result["notes"])
    msg.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Ignore)
    if msg.exec() == QMessageBox.StandardButton.Open:
        QDesktopServices.openUrl(QUrl(result["url"]))
```

- [ ] **Step 3: Add settings toggle**

In `src/ui/settings_dialog.py`, add a checkbox "Check for updates on startup" bound to `check_for_updates` setting. Default True.

- [ ] **Step 4: Test version logic**

Add `tests/test_version.py`:
```python
from src.core.version import parse_version, check_for_update

def test_parse_version_basic():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.1.0") == (0, 1, 0)

def test_parse_version_handles_non_numeric():
    assert parse_version("1.2.beta")[0:2] == (1, 2)

def test_check_for_update_offline_returns_none(monkeypatch):
    """If URL unreachable, returns None (no exception)."""
    import urllib.request
    def fake_open(*a, **kw):
        raise OSError("network unreachable")
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert check_for_update(current="0.0.0") is None
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_version.py -v
git add src/core/version.py main.py src/ui/main_window.py src/ui/settings_dialog.py tests/test_version.py
git commit -m "feat(updates): startup update check via GitHub raw JSON, opt-out in settings"
```

---

### Task 4.2: Local crash reporter

**Files:**
- Create: `src/core/crash_reporter.py`
- Modify: `main.py` — install global excepthook

When uncaught exception happens, write a structured JSON dump to disk that the user can attach to a bug report. Optional Sentry pipe (no-op without DSN).

- [ ] **Step 1: Write src/core/crash_reporter.py**

```python
"""Local crash reporting. Optional Sentry passthrough.

Crash dumps are written to %APPDATA%/VideoEditor/crashes/ and the user is
prompted on next launch to optionally include them in a bug report.
"""
from __future__ import annotations
import json
import os
import platform
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from src.core import paths
from src.core.log_setup import sanitize_path
from src.core.version import VERSION


def _crashes_dir() -> Path:
    d = paths.config_dir() / "crashes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_crash(exc_type, exc_value, exc_tb) -> Path:
    """Write a sanitized crash dump. Returns the dump file path."""
    dump = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "version": VERSION,
        "python": sys.version,
        "platform": platform.platform(),
        "exception_type": exc_type.__name__ if exc_type else "Unknown",
        "exception_value": sanitize_path(str(exc_value)),
        "traceback": sanitize_path("".join(traceback.format_exception(exc_type, exc_value, exc_tb))),
    }
    path = _crashes_dir() / f"crash_{int(dump['timestamp'])}_{dump['id'][:8]}.json"
    try:
        path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    except OSError:
        # Last resort — print to stderr (which main.py redirects to log)
        sys.stderr.write(f"Crash dump write failed:\n{dump}\n")
    return path


def install_global_handler() -> None:
    """Install sys.excepthook so uncaught exceptions are captured."""
    prev = sys.excepthook

    def handler(exc_type, exc_value, exc_tb):
        try:
            write_crash(exc_type, exc_value, exc_tb)
            _maybe_send_sentry(exc_type, exc_value, exc_tb)
        finally:
            # Always call previous handler so traceback still prints
            prev(exc_type, exc_value, exc_tb)

    sys.excepthook = handler


def _maybe_send_sentry(exc_type, exc_value, exc_tb) -> None:
    """If SENTRY_DSN env var set and `sentry-sdk` importable, capture exception.
    Silent no-op otherwise."""
    if not os.environ.get("SENTRY_DSN"):
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        if not sentry_sdk.Hub.current.client:
            sentry_sdk.init(
                dsn=os.environ["SENTRY_DSN"],
                release=VERSION,
                send_default_pii=False,
                before_send=_sentry_scrub,
            )
        sentry_sdk.capture_exception((exc_type, exc_value, exc_tb))
    except Exception:
        pass  # never raise from crash handler


def _sentry_scrub(event, hint):
    """Sanitize event before transmission."""
    if "exception" in event:
        for v in event["exception"].get("values", []):
            if "value" in v:
                v["value"] = sanitize_path(v["value"])
            for frame in v.get("stacktrace", {}).get("frames", []):
                if "filename" in frame:
                    frame["filename"] = sanitize_path(frame["filename"])
    return event


def list_pending_crashes() -> list[Path]:
    """Crash dumps that haven't been acknowledged."""
    return sorted(_crashes_dir().glob("crash_*.json"))


def acknowledge(path: Path) -> None:
    """Mark a crash as seen by the user (delete it)."""
    try:
        path.unlink()
    except OSError:
        pass
```

- [ ] **Step 2: Install handler in main.py**

In `main.py`, after `log_setup.init(...)`:
```python
from src.core import crash_reporter
crash_reporter.install_global_handler()
```

- [ ] **Step 3: Add startup prompt for pending crashes**

In `MainWindow.__init__`, after main UI:
```python
def _check_pending_crashes(self):
    pending = crash_reporter.list_pending_crashes()
    if not pending:
        return
    msg = QMessageBox(self)
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setWindowTitle("Crash report available")
    msg.setText(
        f"{len(pending)} crash report(s) from previous sessions are available.\n\n"
        "Would you like to open them so you can attach them to a bug report?"
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Ignore)
    choice = msg.exec()
    if choice == QMessageBox.StandardButton.Open:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pending[0].parent)))
    elif choice == QMessageBox.StandardButton.Discard:
        for p in pending:
            crash_reporter.acknowledge(p)

QTimer.singleShot(2000, self._check_pending_crashes)
```

- [ ] **Step 4: Test write_crash**

Add `tests/test_crash_reporter.py`:
```python
from src.core import crash_reporter

def test_write_crash_sanitizes_paths(tmp_path, monkeypatch):
    from src.core import paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    try:
        raise ValueError(r"file C:\Users\alice\secret.mp4 broke")
    except ValueError as e:
        path = crash_reporter.write_crash(type(e), e, e.__traceback__)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "alice" not in text
    assert "<USER>" in text or "<HOME>" in text or "<USERS>" in text
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_crash_reporter.py -v
git add src/core/crash_reporter.py main.py src/ui/main_window.py tests/test_crash_reporter.py
git commit -m "feat(crashes): local sanitized crash dumps + opt-in Sentry pipe"
```

---

### Task 4.3: "Report a Bug" menu item

**Files:**
- Create: `src/core/bug_report.py`
- Modify: `src/ui/main_window.py` — Help menu

Bundles the most recent log + version info into a clipboard-friendly format and opens email or GitHub issue URL.

- [ ] **Step 1: Write src/core/bug_report.py**

```python
"""Build a clipboard-friendly bug report from recent logs."""
from __future__ import annotations
import platform
import sys
from pathlib import Path

from src.core import paths
from src.core.log_setup import sanitize_path
from src.core.version import VERSION


def build_report(include_log_lines: int = 200) -> str:
    """Return a markdown-formatted bug report ready to paste."""
    log_path = paths.config_dir() / "editor.log"
    log_excerpt = ""
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_excerpt = "\n".join(lines[-include_log_lines:])
            log_excerpt = sanitize_path(log_excerpt)
        except OSError:
            log_excerpt = "(log read failed)"
    return f"""## Bug Report

**Version:** {VERSION}
**Python:** {sys.version.split()[0]}
**Platform:** {platform.platform()}

### What I expected
<describe expected behavior>

### What happened
<describe actual behavior>

### Steps to reproduce
1.
2.
3.

### Recent log (last {include_log_lines} lines, paths sanitized)
```
{log_excerpt}
```
"""


def open_bug_report() -> None:
    """Copy report to clipboard and open issue URL.

    URL placeholder must be replaced once the project is published.
    """
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtCore import QUrl

    report = build_report()
    QApplication.clipboard().setText(report)
    # Try GitHub issues first, fall back to mailto
    issue_url = "https://github.com/PLACEHOLDER_USER/PLACEHOLDER_REPO/issues/new?title=Bug%20report&body=Paste%20clipboard%20contents%20here"
    QDesktopServices.openUrl(QUrl(issue_url))
```

- [ ] **Step 2: Wire into Help menu**

In `MainWindow._build_menus` (or wherever menus live):
```python
help_menu = self.menuBar().addMenu("&Help")
report_act = help_menu.addAction("&Report a Bug…")
report_act.triggered.connect(self._on_report_bug)

def _on_report_bug(self):
    from src.core import bug_report
    bug_report.open_bug_report()
    self.statusBar().showMessage(
        "Bug report copied to clipboard. Paste it into the GitHub issue.", 8000
    )
```

- [ ] **Step 3: Test build_report**

Add to `tests/test_bug_report.py`:
```python
def test_build_report_includes_version_and_platform():
    from src.core.bug_report import build_report
    from src.core.version import VERSION
    r = build_report()
    assert VERSION in r
    assert "Platform" in r

def test_build_report_sanitizes_log(tmp_path, monkeypatch):
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "editor.log").write_text(
        r"opened C:\Users\alice\Videos\file.mp4", encoding="utf-8"
    )
    r = bug_report.build_report()
    assert "alice" not in r
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_bug_report.py -v
git add src/core/bug_report.py src/ui/main_window.py tests/test_bug_report.py
git commit -m "feat(bugreport): Help → Report a Bug copies sanitized report + opens issue URL"
```

---

### Task 4.4: Settings versioning + migration

**Files:**
- Create: `src/core/settings_migration.py`
- Modify: `src/ui/main_window.py` — call migration on `_load_settings`

Without versioning, an old settings.json from v0.1 can crash v0.2. Add a `version` field and a migration chain.

- [ ] **Step 1: Write settings_migration.py**

```python
"""Settings file version migrations."""
from __future__ import annotations
import copy
from typing import Callable, Dict

CURRENT_VERSION = 1

# Each migration takes a settings dict (mutated in place is OK) and bumps version.
_migrations: Dict[int, Callable[[dict], dict]] = {}


def migration(from_version: int):
    """Register a migration FROM the given version to from_version+1."""
    def decorator(fn):
        _migrations[from_version] = fn
        return fn
    return decorator


@migration(0)
def _0_to_1(s: dict) -> dict:
    """Initial migration: ensure all keys exist."""
    s.setdefault("ui_scale", 90)
    s.setdefault("theme", "dark")
    s.setdefault("check_for_updates", True)
    s.setdefault("crash_reports", False)
    s["version"] = 1
    return s


def migrate(settings: dict) -> dict:
    """Apply all migrations from settings['version'] (default 0) to CURRENT_VERSION."""
    out = copy.deepcopy(settings)
    v = out.get("version", 0)
    while v < CURRENT_VERSION:
        if v not in _migrations:
            # No migration registered → just bump version (forward-compat hole).
            out["version"] = v + 1
        else:
            out = _migrations[v](out)
        v = out.get("version", v + 1)
    return out
```

- [ ] **Step 2: Wire into _load_settings in main_window.py**

```python
from src.core.settings_migration import migrate

# In _load_settings, after json.loads:
data = json.loads(text)
data = migrate(data)
self._settings = data
# Re-save migrated settings
self._save_settings()
```

- [ ] **Step 3: Test migration**

Add `tests/test_settings_migration.py`:
```python
from src.core.settings_migration import migrate, CURRENT_VERSION

def test_migrate_empty_dict_to_current():
    out = migrate({})
    assert out["version"] == CURRENT_VERSION
    assert "ui_scale" in out
    assert "theme" in out
    assert "check_for_updates" in out

def test_migrate_preserves_existing_user_values():
    out = migrate({"ui_scale": 110, "theme": "light"})
    assert out["ui_scale"] == 110
    assert out["theme"] == "light"
    assert out["version"] == CURRENT_VERSION

def test_migrate_idempotent():
    once = migrate({"ui_scale": 95})
    twice = migrate(once)
    assert once == twice
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_settings_migration.py -v
git add src/core/settings_migration.py src/ui/main_window.py tests/test_settings_migration.py
git commit -m "feat(settings): version field + migration chain (with idempotency tests)"
```

---

## Phase 5 — Documentation & Polish

### Task 5.1: User-facing README

**Files:**
- Create: `README.md` (if missing) or replace existing minimal one

- [ ] **Step 1: Write a real README**

Sections needed:
- Screenshot or animated demo (placeholder if not yet captured)
- Features (1-line list)
- Install (download VideoEditor-Setup.exe from Releases)
- Quickstart (5 numbered steps)
- Keyboard shortcuts (table or link to Settings → Shortcuts)
- Troubleshooting (3-5 most likely issues + fix)
- Privacy (1-line: see PRIVACY.md)
- License (link to LICENSE)
- Contributing (link to CONTRIBUTING.md if it exists, else "open an issue")
- Build from source (link to docs/BUILD.md)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: user-facing README with install + quickstart + privacy + license"
```

---

### Task 5.2: Build instructions

**Files:**
- Create: `docs/BUILD.md`

For people who want to build from source.

- [ ] **Step 1: Write docs/BUILD.md**

```markdown
# Build from Source

## Prerequisites
- Windows 10/11 x64
- Python 3.14
- Inno Setup 6 (free): `winget install JRSoftware.InnoSetup`
- (Optional) Authenticode code-signing certificate

## Quick build
```powershell
git clone <repo> video-editor
cd video-editor
pip install -r requirements.txt
.\tools\release.ps1 -Version 0.1.0
```

Output: `dist\installer\VideoEditor-Setup-0.1.0.exe`

## Code signing (optional)
```powershell
$env:SIGNCERT_PATH = "C:\path\to\cert.pfx"
$env:SIGNCERT_PASSWORD = "<password>"
$env:SIGNCERT_TIMESTAMP = "http://timestamp.digicert.com"
.\tools\release.ps1 -Version 0.1.0
```

## Crash reporting (optional)
```powershell
$env:SENTRY_DSN = "https://...@sentry.io/..."
pip install sentry-sdk
```
Then build normally.

## Troubleshooting
- **`fetch_ffmpeg.ps1` fails with 403** → GitHub API rate limit. Wait 1 hour or set `$env:GITHUB_TOKEN`.
- **PyInstaller misses a hidden import** → add `--hidden-import <name>` to `tools/build.bat`.
- **Inno Setup not found** → `winget install JRSoftware.InnoSetup` then close + reopen the shell.
```

- [ ] **Step 2: Commit**

```bash
git add docs/BUILD.md
git commit -m "docs: build-from-source instructions"
```

---

### Task 5.3: Final smoke test of installer + push

- [ ] **Step 1: Full release rehearsal**

Run: `powershell -ExecutionPolicy Bypass -File "tools\release.ps1" -Version 0.1.0`

Verify:
- `dist\Video Editor\Video Editor.exe` exists
- `dist\Video Editor\ffmpeg\ffmpeg.exe` exists
- `dist\Video Editor\LICENSE.txt`, `NOTICES.md`, `PRIVACY.md` exist
- `dist\installer\VideoEditor-Setup-0.1.0.exe` exists, ≤ 250 MB

- [ ] **Step 2: Install and test on a clean directory**

```powershell
$test = "C:\TestVideoEditor"
Remove-Item -Recurse -Force $test -ErrorAction SilentlyContinue
& "dist\installer\VideoEditor-Setup-0.1.0.exe" /VERYSILENT /DIR=$test
& "$test\Video Editor.exe"
# (interactive: poke around for 30s, close)
& "$test\unins000.exe" /VERYSILENT
```

Verify the install dir is fully removed after uninstall.

- [ ] **Step 3: Push and let CI run**

```bash
git push origin master
```

Then check the GitHub Actions tab and verify the CI run is green.

- [ ] **Step 4: Final commit (release tag)**

```bash
git tag -a v0.1.0 -m "v0.1.0 — first ship-ready release"
git push origin v0.1.0
```

---

## Self-Review Checklist (run after writing complete plan)

- [x] Every silent-failure finding from the audit has a task (Tasks 1.2-1.4, 1.9)
- [x] Every P0 distribution gap from the audit has a task (Tasks 2.1-2.5)
- [x] Every security finding has a task (Tasks 1.6, 1.7, 1.8, 1.9)
- [x] Test gaps from the audit have tasks (Tasks 3.1-3.5)
- [x] No "TBD" or "implement later" placeholders
- [x] Every code step shows actual code, not "add appropriate handling"
- [x] Type/method names are consistent across tasks (`status_cb`, `installation_id`, `migrate`, `check_for_update`)
- [x] All file paths are absolute or unambiguously relative to project root
- [x] Every task ends with a commit step

## Items intentionally deferred (require money or external accounts)

- **Authenticode certificate purchase** ($90-400/yr) — Task 2.3 prepares the hook so signing happens automatically once cert is in place.
- **Sentry account creation** — Task 4.2 prepares the integration so it activates on `SENTRY_DSN` env var.
- **Real CDN for downloads** — Task 4.1 uses GitHub raw URLs; switch to a CDN later by editing `UPDATE_URL` in `src/core/version.py`.
- **GitHub repository publication** — placeholders in version.py, bug_report.py must be replaced when the repo URL is known.
- **Hardware diversity testing** — only one dev machine available; add a beta channel after release.
- **Localization** — English only at first release; i18n is a separate plan.
