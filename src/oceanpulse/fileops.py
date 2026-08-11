"""Cross-platform atomic file replacement.

`os.replace` is atomic on both POSIX and Windows, but the two disagree about a
file that another process currently has open:

* **POSIX** unlinks the directory entry while the open handle keeps pointing at
  the old inode. The replacement succeeds, and the reader carries on seeing the
  old contents until it reopens.
* **Windows** refuses outright with `PermissionError` / `WinError 32`, because a
  file opened without `FILE_SHARE_DELETE` is locked against deletion.

That difference breaks a specific, ordinary action: rebuilding the port database
or the ocean mask while OceanPulse is running. The interface holds
`ports.sqlite` open read-only, so on Windows the swap at the end of a rebuild
fails and — before this helper — took the whole build down with an unhandled
exception after a minute of downloading.

The fallback here moves the existing file aside first, which Windows permits far
more often than deletion, then puts the new file in place. If even that is
refused, the freshly built file is preserved next to the target and the caller
gets an actionable error rather than losing the work.
"""

from __future__ import annotations

import os
from pathlib import Path

from .logging_setup import get_logger

log = get_logger(__name__)


class ReplaceInUseError(RuntimeError):
    """The destination is locked by another process (Windows). Work is preserved."""


def atomic_replace(source: Path, destination: Path) -> None:
    """Move `source` onto `destination`, atomically where the OS allows it.

    Raises `ReplaceInUseError` when the destination is locked and cannot be
    moved aside. The built file is left in place so nothing has to be
    re-downloaded.
    """
    source = Path(source)
    destination = Path(destination)

    try:
        source.replace(destination)
        return
    except PermissionError:
        # Windows: destination is open elsewhere. Try moving it aside instead.
        pass

    aside = destination.with_suffix(destination.suffix + ".replaced")
    try:
        if aside.exists():
            aside.unlink()
    except OSError:
        pass

    try:
        os.replace(destination, aside)
    except OSError as exc:
        raise ReplaceInUseError(
            f"{destination.name} is open in another program and cannot be replaced. "
            f"The new file is ready at {source.name} — stop OceanPulse and either "
            f"rename it over {destination.name}, or start OceanPulse again and "
            f"re-run the rebuild."
        ) from exc

    try:
        source.replace(destination)
    except OSError as exc:
        # Put the original back rather than leaving no file at all.
        try:
            os.replace(aside, destination)
        except OSError:
            pass
        raise ReplaceInUseError(
            f"could not move the new {destination.name} into place: {exc}"
        ) from exc

    try:
        aside.unlink()
    except OSError:
        # A stale .replaced file is harmless; the running process may still
        # hold the old inode open and Windows will refuse until it exits.
        log.debug("left %s behind; it can be deleted at any time", aside.name)


def remove_quietly(path: Path) -> None:
    """Delete a path if present, ignoring the ways that can fail."""
    try:
        Path(path).unlink()
    except (OSError, FileNotFoundError):
        pass
