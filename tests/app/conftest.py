"""Per-package conftest for ``tests/app``.

The bikemap-DB fixtures (``tiny_bikemap_db``, ``divergent_bikemap_db``,
``tiny_bikemap_db_with_pois``) were lifted to ``tests/conftest.py`` so
they can be shared with ``tests/prep`` as well (Plan 2D Task 1). They
are inherited automatically by pytest's conftest discovery — no need to
re-declare them here.
"""
from __future__ import annotations
