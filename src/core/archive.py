"""Auto-archive original clips after trim.

Detects the year/month folder structure in the source path automatically — no
configurable source roots. Assumes clips live under `<anything>/YYYY/MM - Name/`
(the structure produced by the OBS sort script), and preserves that relative
path inside the archive folder. Clips that don't match the pattern fall back to
an `Unsorted` subfolder so they aren't silently lost.
"""
import re
import shutil
from pathlib import Path

# Matches a 4-digit year folder (1900-2099 in practice).
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def detect_relative_path(file_path: Path) -> Path | None:
    """Walk up the path looking for a YYYY folder.

    If found, return the path starting at that year folder down to the file.
    Otherwise return None — caller decides the fallback behavior.

    Example:
        detect_relative_path(Path('D:/Clips/MKV Clips/2026/04 - April/foo.mkv'))
        -> Path('2026/04 - April/foo.mkv')
    """
    parts = file_path.parts
    for i, part in enumerate(parts):
        if _YEAR_RE.match(part):
            return Path(*parts[i:])
    return None


def _unique_destination(dest: Path) -> Path:
    """Append ' (N)' before the extension until the path doesn't exist.
    Mirrors the collision policy in sort_clips.ps1 for consistency."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def archive_original(
    source_file: Path,
    archive_root: Path,
    unsorted_folder: str = "Unsorted",
) -> Path:
    """Move `source_file` into `archive_root`, auto-preserving year/month structure.

    - If a YYYY ancestor folder is found in the source path, the relative path
      from that year folder is preserved under the archive root.
    - Otherwise the file is placed under `<archive_root>/<unsorted_folder>/`.
    - On filename collision, appends ' (1)', ' (2)', etc.
    - If the source is already inside the archive, the move is skipped and the
      original path is returned unchanged.

    Raises FileNotFoundError if `source_file` does not exist.
    """
    source_file = Path(source_file).resolve()
    archive_root = Path(archive_root).resolve()

    if not source_file.exists():
        raise FileNotFoundError(source_file)

    # No-op: source is already inside the archive root.
    try:
        source_file.relative_to(archive_root)
        return source_file
    except ValueError:
        pass

    rel = detect_relative_path(source_file)
    dest = archive_root / (rel if rel is not None else Path(unsorted_folder) / source_file.name)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _unique_destination(dest)
    shutil.move(str(source_file), str(dest))
    return dest
