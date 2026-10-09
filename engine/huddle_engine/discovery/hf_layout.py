"""The Hugging Face cache layout on Windows.

``snapshot_download`` lays a repo out as ``blobs/<sha>`` plus ``snapshots/<rev>/<file>`` symlinks
to the blobs (huggingface_hub 2.x adds a second hop: ``blobs/<sha>`` → ``../../blobs/<ab>/<sha>``
in the cache root's shared blob store). Python can *create* those links on Windows (Developer
Mode or an unprivileged-symlink policy), yet on some machines Windows refuses to *follow* them in
certain folders; seen on a PC where links under ``AppData\\Roaming`` and ``AppData\\Local`` were
dead while the same links under ``Documents`` worked. CTranslate2 then fails with "Unable to open
file 'model.bin'" although every byte was downloaded.

So on Windows a snapshot is dereferenced after download (and healed at scan time): each symlink
that does not resolve is replaced by the file it points to, moved into place. Nothing is copied;
the snapshot afterwards holds plain files, which huggingface_hub accepts as "already downloaded".
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

MAX_HOPS = 8


def _final_target(link: Path) -> Path | None:
    """Follow a chain of symlinks by hand (relative targets resolve against the link's folder)
    and return the regular file at the end, or None when the chain is broken."""
    cur = link
    for _ in range(MAX_HOPS):
        try:
            raw = os.readlink(cur)
        except OSError:
            return None
        nxt = Path(raw) if os.path.isabs(raw) else cur.parent / raw
        cur = Path(os.path.normpath(nxt))
        if not cur.is_symlink():
            try:
                return cur if cur.lstat() and not cur.is_dir() else None
            except OSError:
                return None
    return None


def dereference_snapshot(snapshot: Path, only_broken: bool = True) -> int:
    """Replace symlinks in ``snapshot`` by their files (moved into place). With ``only_broken``
    (the default, and the behaviour on Windows) links the OS can follow are left alone, so a
    Mac or a PC where symlinks work keeps the space-saving layout. Returns the number of files
    materialised."""
    if only_broken and os.name != "nt":
        return 0
    if not snapshot.is_dir():
        return 0
    n = 0
    for p in list(snapshot.rglob("*")):
        if not p.is_symlink():
            continue
        if only_broken and p.exists():
            continue
        target = _final_target(p)
        if target is None:
            log.warning("model file %s points nowhere; leaving it", p)
            continue
        try:
            p.unlink()
            shutil.move(str(target), str(p))
            n += 1
        except OSError:
            log.exception("could not materialise %s", p)
    if n:
        log.info("materialised %d model file(s) in %s (symlinks did not resolve)", n, snapshot)
    return n
