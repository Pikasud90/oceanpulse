"""Offline gazetteer search.

FTS5 `MATCH` takes a query *language*, not a literal string. Raw user input
reaches it as syntax, so `St. John's`, `a OR b`, or a bare `*` raise
`sqlite3.OperationalError` from inside an autocomplete callback - which fires
on every keystroke. Input is therefore stripped of operator characters, split
into tokens, and each token quoted; only the final token gets a prefix
wildcard, so search-as-you-type works without turning every word into a
wildcard scan.

Ranking is by match tier and then population, not by BM25. BM25 normalises by
document length, and a well-known port carries a long list of alternate names,
so a relevance-only ordering ranks famous places *worse* the more famous they
are.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger
from ..math_engine import haversine_km
from .build import fold

log = get_logger(__name__)

# Anything that is not a letter, digit or space is an FTS5 operator or noise.
_STRIP = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

MAX_TOKENS = 6


def build_match_query(user_input: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression.

    Returns None when nothing searchable survives, which the caller treats as
    "no results" rather than running a query that matches everything.
    """
    folded = fold(user_input or "")
    cleaned = _SPACES.sub(" ", _STRIP.sub(" ", folded)).strip()
    if not cleaned:
        return None
    tokens = [token for token in cleaned.split(" ") if token][:MAX_TOKENS]
    if not tokens:
        return None
    parts = [f'"{token}"' for token in tokens[:-1]]
    # Prefix-match only the token still being typed.
    parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)


class GazetteerStore:
    """Read-only access to the offline port index."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _connection(self) -> sqlite3.Connection | None:
        if not self.available:
            return None
        connection = getattr(self._local, "connection", None)
        if connection is None:
            try:
                # Read-only: the interface has no business modifying it.
                connection = sqlite3.connect(
                    f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
                )
                connection.row_factory = sqlite3.Row
            except sqlite3.Error as exc:
                log.warning("cannot open gazetteer at %s: %s", self.path, exc)
                return None
            self._local.connection = connection
        return connection

    def entry_count(self) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            row = connection.execute("SELECT COUNT(*) AS n FROM ports").fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.Error:
            return 0

    def search(self, text: str, limit: int = 12) -> list[dict[str, Any]]:
        """Rank matching places. Never raises on hostile input."""
        connection = self._connection()
        if connection is None:
            return []
        match_query = build_match_query(text)
        if match_query is None:
            return []

        folded_input = fold(text).strip()
        try:
            rows = connection.execute(
                """
                SELECT p.*
                FROM ports_fts f
                JOIN ports p ON p.rowid = f.rowid
                WHERE ports_fts MATCH ?
                LIMIT 400
                """,
                (match_query,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # Should be unreachable given the escaping above; if it ever is
            # reached, an empty result beats a stack trace in a keystroke
            # callback.
            log.warning("FTS query failed for %r: %s", text, exc)
            return []

        # Popular places must not be truncated away before ranking runs, so
        # the candidate pool is topped up by population as well as relevance.
        try:
            popular = connection.execute(
                """
                SELECT * FROM ports
                WHERE name_folded LIKE ? || '%'
                ORDER BY population DESC
                LIMIT 60
                """,
                (folded_input,),
            ).fetchall()
        except sqlite3.Error:
            popular = []

        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for row in list(rows) + list(popular):
            record = dict(row)
            if record["port_id"] in seen:
                continue
            seen.add(record["port_id"])
            record["rank_tier"] = _tier(record["name_folded"], folded_input)
            candidates.append(record)

        # Population outranks source, and only then does a port beat a city.
        # Ordering source first looks reasonable - this is a marine gazetteer,
        # so prefer real ports - but it means a small harbour outranks a large
        # city with the same name: `sydn` returns Sydney, Nova Scotia rather
        # than Sydney, Australia. Where both sources describe the same place
        # the populations are identical, so the port still wins the tie and
        # brings its harbour metadata with it.
        candidates.sort(
            key=lambda r: (
                r["rank_tier"],
                -int(r.get("population") or 0),
                0 if r["source"] == "wpi" else 1,
                len(r["port_name"]),
            )
        )
        return candidates[:limit]

    def get(self, port_id: str) -> dict[str, Any] | None:
        connection = self._connection()
        if connection is None:
            return None
        row = connection.execute(
            "SELECT * FROM ports WHERE port_id = ?", (port_id,)
        ).fetchone()
        return dict(row) if row else None

    def nearest(self, latitude: float, longitude: float, limit: int = 5) -> list[dict[str, Any]]:
        """Closest gazetteer entries to a point.

        A bounding-box prefilter keeps this off a full scan, then exact
        great-circle distance decides the order.
        """
        connection = self._connection()
        if connection is None:
            return []
        for pad in (2.0, 5.0, 15.0, 45.0):
            rows = connection.execute(
                "SELECT * FROM ports WHERE latitude BETWEEN ? AND ? "
                "AND longitude BETWEEN ? AND ? LIMIT 2000",
                (latitude - pad, latitude + pad, longitude - pad, longitude + pad),
            ).fetchall()
            if rows:
                scored = []
                for row in rows:
                    record = dict(row)
                    record["distance_km"] = haversine_km(
                        latitude, longitude, record["latitude"], record["longitude"]
                    )
                    scored.append(record)
                scored.sort(key=lambda r: r["distance_km"])
                return scored[:limit]
        return []

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None


def _tier(name_folded: str, query: str) -> int:
    """Lower is better. Mirrors how a person expects a search box to behave."""
    if not query:
        return 4
    if name_folded == query:
        return 0
    if name_folded.startswith(query):
        return 1
    if any(word.startswith(query) for word in name_folded.split()):
        return 2
    if query in name_folded:
        return 3
    return 4  # matched only via alternate names or another indexed column


def format_label(record: dict[str, Any]) -> str:
    """Human-readable one-liner for a search result."""
    parts = [record.get("port_name", "")]
    country = record.get("country_name") or record.get("country_code") or ""
    if country:
        parts.append(country)
    label = ", ".join(part for part in parts if part)
    if record.get("source") == "wpi":
        water = record.get("water_body")
        if water:
            label = f"{label} · {water}"
    return label
