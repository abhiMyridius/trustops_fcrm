"""``FolderScanner`` — safe, non-vanishing directory listing for uploads.

Scope & functionality
---------------------
Replaces the ad-hoc ``_scan_folder`` free function that used to live in
``sources/uploads_source.py``. Two hardening fixes over that version
(§1.3/§1.7 I8):

* **Containment**: every discovered path must resolve to somewhere inside
  the scanned root. A symlink is never followed — ``path.is_symlink()`` is
  rejected outright (``ScanEntry.status == "symlink_rejected"``) whether or
  not it would have escaped, since following symlinks at all is the risk
  the caller is trying to avoid.
* **Subdirectories never silently vanish**: when ``recursive=False`` (the
  default), a directory found directly under the root is reported as a
  ``subdirectory_skipped`` entry instead of being passed over with a bare
  ``continue`` — the caller (``UploadsIngestionSource``) turns that into a
  ``FAILED`` row so an operator can see exactly what was excluded, not a
  silent gap between "files on disk" and "documents ingested".

``scan()`` returns ``ScanEntry`` objects rather than bare paths so a
caller can tell a normal file from something that was rejected/skipped
without re-deriving that from the filesystem a second time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from evoq_ingestion.core.codes import WarningCode

logger = logging.getLogger(__name__)

__all__ = ["FolderScanner", "ScanEntry"]


@dataclass(frozen=True, slots=True)
class ScanEntry:
    """One thing the scanner found under the root.

    ``status`` is ``"ok"`` for a regular file to extract, or the
    ``WarningCode`` (as its string value) explaining why it was excluded
    (``symlink_rejected`` / ``subdirectory_skipped``).
    """

    path: Path
    status: str = "ok"

    @property
    def ok(self) -> bool:
        """True when this entry is a regular file to extract."""
        return self.status == "ok"


class FolderScanner:
    """List a folder's files.

    Never follows symlinks and never vanishes a subdirectory without a
    trace.
    """

    def __init__(self, *, recursive: bool = False, follow_symlinks: bool = False) -> None:
        """Construct a scanner.

        ``follow_symlinks`` is accepted for the constructor's own
        documentation value but is always effectively ``False`` — a
        symlink is rejected, never traversed, regardless of this flag,
        since the platform never wants uploads content to leave the
        scanned root via a link. It stays a keyword so a future, explicitly
        audited relaxation has a single place to change.
        """
        self._recursive = recursive
        self._follow_symlinks = follow_symlinks

    @property
    def recursive(self) -> bool:
        """Whether subdirectories are descended into."""
        return self._recursive

    def scan(self, root: Path) -> list[ScanEntry]:
        """Scan *root* (already validated to exist/be a directory by the caller).

        Returns entries in a deterministic (sorted-by-path) order so
        downstream batching/reporting is reproducible.
        """
        resolved_root = root.resolve()
        entries = list(self._walk(root, resolved_root))
        entries.sort(key=lambda e: e.path.as_posix())
        return entries

    # ------------------------------------------------------------- internals
    def _walk(self, directory: Path, resolved_root: Path) -> list[ScanEntry]:
        entries: list[ScanEntry] = []
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            logger.warning("Could not list directory %s: %s", directory, exc)
            return entries

        for child in children:
            if child.is_symlink():
                if not self._is_contained(child, resolved_root):
                    logger.warning("Symlink escapes scan root, rejecting: %s", child)
                else:
                    logger.warning("Symlink rejected (never followed): %s", child)
                entries.append(ScanEntry(path=child, status=str(WarningCode.symlink_rejected)))
                continue

            is_dir = child.is_dir()
            if not self._is_contained(child, resolved_root):
                # Not a symlink but somehow escapes containment (e.g. a
                # race with something mutating the tree mid-scan) — treat
                # exactly like a rejected symlink rather than trusting it.
                entries.append(ScanEntry(path=child, status=str(WarningCode.symlink_rejected)))
                continue

            if is_dir:
                if self._recursive:
                    entries.extend(self._walk(child, resolved_root))
                else:
                    skipped = str(WarningCode.subdirectory_skipped)
                    entries.append(ScanEntry(path=child, status=skipped))
                continue

            if child.is_file():
                entries.append(ScanEntry(path=child, status="ok"))
        return entries

    @staticmethod
    def _is_contained(path: Path, resolved_root: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved == resolved_root or resolved.is_relative_to(resolved_root)


def scan_files(root: Path, *, recursive: bool = False) -> list[Path]:
    """Convenience wrapper: just the extractable file paths (back-compat shape)."""
    return [e.path for e in FolderScanner(recursive=recursive).scan(root) if e.ok]
