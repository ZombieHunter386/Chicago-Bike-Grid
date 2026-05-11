# prep/db/treatments_loader.py
"""Parse treatment markdown files and write rows to the treatments table.

This module owns the markdown parsing concern only. It calls the public
`DbBuilder.insert_treatments` method to write — never touches private
DB internals.
"""
from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from prep.db.builder import DbBuilder

logger = logging.getLogger(__name__)


def load_treatments(treatments_dir: Path, builder: DbBuilder) -> int:
    """Load treatments/*.md files into the `treatments` table.

    Returns the number of treatments loaded. Skips files with malformed
    frontmatter, logging a warning rather than failing the whole pipeline.
    """
    if not treatments_dir.exists():
        return 0

    rows = []
    skipped: list[tuple[Path, Exception]] = []
    for md_path in sorted(treatments_dir.glob("*.md")):
        try:
            post = frontmatter.load(md_path)
            slug = md_path.stem
            meta = post.metadata
            rows.append((
                slug,
                meta.get("type", "unknown"),
                str(meta["ward"]) if meta.get("ward") is not None else None,
                float(meta["location_lat"]) if meta.get("location_lat") is not None else None,
                float(meta["location_lng"]) if meta.get("location_lng") is not None else None,
                meta.get("photo_path"),
                meta.get("source_url"),
                meta.get("summary"),
                post.content,
            ))
        except (ValueError, KeyError) as e:
            skipped.append((md_path, e))

    for path, err in skipped:
        logger.warning("skipping malformed treatment %s: %s", path.name, err)

    if not rows:
        return 0

    return builder.insert_treatments(rows)
