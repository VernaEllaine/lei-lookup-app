"""Local copy of the GLEIF golden copy (LEI records) and ISIN-LEI mapping.

Loading the full files into SQLite lets name searches, LEI validation and
ISIN lookups run locally instead of going through the rate-limited GLEIF API.

Each load writes a new ``gleif-<publish>.db`` file and then points the
``current`` file at it, so a refresh can run while the server is up: open
connections switch to the new file on their next query.

Usage:
    python -m backend.gleif_local load            # download latest + build
    python -m backend.gleif_local load --lei-zip path/to/lei2.csv.zip --isin-zip path/to/isin.zip
    python -m backend.gleif_local status
"""

from __future__ import annotations

import csv
import glob
import io
import os
import re
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import datetime

from rapidfuzz import fuzz


def _default_data_dir() -> str:
    if os.environ.get("GLEIF_DATA_DIR"):
        return os.environ["GLEIF_DATA_DIR"]
    if os.environ.get("DATA_DIR"):
        return os.path.join(os.environ["DATA_DIR"], "gleif")
    # Keep the large DB out of the project folder (which may be synced).
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "lei-lookup", "gleif")


DATA_DIR = _default_data_dir()

GOLDEN_COPY_LATEST = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
ISIN_LATEST = "https://mapping.gleif.org/api/v2/isin-lei/latest"

# How many FTS hits to re-rank before returning the top results
_CANDIDATE_POOL = 300
_BATCH = 20_000

_local = threading.local()


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _pointer_path() -> str:
    return os.path.join(DATA_DIR, "current")


def _current_db_path() -> str | None:
    try:
        with open(_pointer_path(), encoding="utf-8") as f:
            name = f.read().strip()
    except OSError:
        return None
    path = os.path.join(DATA_DIR, name)
    return path if name and os.path.exists(path) else None


def _get_conn() -> sqlite3.Connection | None:
    """Return a thread-local read-only connection to the current DB, or None."""
    path = _current_db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    if conn is not None:
        conn.close()
        _local.conn = None
    if path is None:
        return None
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _local.conn = conn
    _local.path = path
    return conn


def is_available() -> bool:
    """True when a local GLEIF copy has been loaded."""
    return _current_db_path() is not None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _fts_query(text: str) -> str | None:
    tokens = _TOKEN.findall(text.lower())
    if not tokens:
        return None
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _record(row: sqlite3.Row) -> dict:
    return {
        "lei": row["lei"],
        "legal_name": row["legal_name"],
        "jurisdiction": row["jurisdiction"],
        "country": row["country"],
        "status": row["entity_status"],
        "registration_status": row["registration_status"],
    }


def search_names(query: str, max_results: int = 10, scorer=None) -> list[dict]:
    """Full-text search over legal and other names of ACTIVE/ISSUED entities.

    Every word in *query* must appear in a name. Hits are re-ranked by
    ``scorer(matched_name) -> float`` (default: rapidfuzz similarity to
    *query*) so the closest names come first.
    """
    conn = _get_conn()
    match = _fts_query(query)
    if conn is None or match is None:
        return []

    rows = conn.execute(
        """
        SELECT e.*, hits.name AS matched_name
        FROM (
            SELECT name, entity_id FROM name_fts
            WHERE name_fts MATCH ? ORDER BY rank LIMIT ?
        ) AS hits
        JOIN entities e ON e.id = hits.entity_id
        """,
        (match, _CANDIDATE_POOL),
    ).fetchall()

    if scorer is None:
        q = query.lower()
        scorer = lambda name: fuzz.token_sort_ratio(q, name.lower())  # noqa: E731

    best: dict[str, tuple[float, sqlite3.Row]] = {}
    for row in rows:
        score = scorer(row["matched_name"])
        prev = best.get(row["lei"])
        if prev is None or score > prev[0]:
            best[row["lei"]] = (score, row)

    ranked = sorted(best.values(), key=lambda s: (-s[0], len(s[1]["legal_name"])))
    return [_record(row) for _, row in ranked[:max_results]]


def get_record(lei: str) -> dict | None:
    """Return the local record for *lei* (any status), or None."""
    conn = _get_conn()
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM entities WHERE lei = ?", (lei.strip().upper(),)
    ).fetchone()
    return _record(row) if row else None


def leis_for_isin(isin: str) -> list[str]:
    """Return the LEIs mapped to *isin* in the local ISIN-LEI file."""
    conn = _get_conn()
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT lei FROM isin_map WHERE isin = ?", (isin.strip().upper(),)
    ).fetchall()
    return [r["lei"] for r in rows]


def status() -> dict:
    """Summary of the loaded data (publish date, counts) for display."""
    conn = _get_conn()
    if conn is None:
        return {"available": False}
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    return {"available": True, **meta}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_NAME_COLUMNS = ["Entity.LegalName"] + [
    f"Entity.{group}.{item}.{n}"
    for group, item in (
        ("OtherEntityNames", "OtherEntityName"),
        ("TransliteratedOtherEntityNames", "TransliteratedOtherEntityName"),
    )
    for n in range(1, 6)
]


def _open_zipped_csv(zip_path: str) -> io.TextIOWrapper:
    z = zipfile.ZipFile(zip_path)
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    return io.TextIOWrapper(z.open(name), encoding="utf-8", newline="")


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY,
            lei TEXT NOT NULL UNIQUE,
            legal_name TEXT NOT NULL DEFAULT '',
            jurisdiction TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            entity_status TEXT NOT NULL DEFAULT '',
            registration_status TEXT NOT NULL DEFAULT ''
        );
        CREATE VIRTUAL TABLE name_fts USING fts5(
            name, entity_id UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        CREATE TABLE isin_map (
            isin TEXT NOT NULL,
            lei TEXT NOT NULL,
            PRIMARY KEY (isin, lei)
        ) WITHOUT ROWID;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def _load_entities(conn: sqlite3.Connection, lei_zip: str, log) -> int:
    reader = csv.DictReader(_open_zipped_csv(lei_zip))
    entities: list[tuple] = []
    names: list[tuple] = []
    count = 0

    def flush() -> None:
        conn.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?,?)", entities)
        conn.executemany("INSERT INTO name_fts (name, entity_id) VALUES (?,?)", names)
        entities.clear()
        names.clear()

    for row in reader:
        count += 1
        entity_status = row["Entity.EntityStatus"]
        reg_status = row["Registration.RegistrationStatus"]
        entities.append((
            count,
            row["LEI"],
            row["Entity.LegalName"],
            row["Entity.LegalJurisdiction"],
            row["Entity.LegalAddress.Country"],
            entity_status,
            reg_status,
        ))
        # Lookups only ever return ACTIVE/ISSUED entities, so only those
        # need to be searchable by name.
        if entity_status == "ACTIVE" and reg_status == "ISSUED":
            seen: set[str] = set()
            for col in _NAME_COLUMNS:
                name = (row.get(col) or "").strip()
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    names.append((name, count))
        if len(entities) >= _BATCH:
            flush()
            if count % 500_000 == 0:
                log(f"  {count:,} LEI records loaded")
    flush()
    log(f"  {count:,} LEI records loaded; optimising name index")
    conn.execute("INSERT INTO name_fts(name_fts) VALUES ('optimize')")
    return count


def _load_isins(conn: sqlite3.Connection, isin_zip: str, log) -> int:
    reader = csv.reader(_open_zipped_csv(isin_zip))
    header = [h.strip().upper() for h in next(reader)]
    lei_i, isin_i = header.index("LEI"), header.index("ISIN")
    batch: list[tuple] = []
    count = 0
    for row in reader:
        batch.append((row[isin_i].strip(), row[lei_i].strip()))
        count += 1
        if len(batch) >= _BATCH:
            conn.executemany("INSERT OR IGNORE INTO isin_map VALUES (?,?)", batch)
            batch.clear()
    conn.executemany("INSERT OR IGNORE INTO isin_map VALUES (?,?)", batch)
    log(f"  {count:,} ISIN mappings loaded")
    return count


def build(lei_zip: str, isin_zip: str | None, publish_date: str = "", log=print) -> str:
    """Build a new local DB from the given zip files and make it current."""
    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    db_name = f"gleif-{stamp}.db"
    path = os.path.join(DATA_DIR, db_name)
    building = path + ".building"
    if os.path.exists(building):
        os.remove(building)

    started = time.time()
    conn = sqlite3.connect(building)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-262144")  # 256 MB
    _create_schema(conn)

    log(f"Loading LEI records from {os.path.basename(lei_zip)}")
    lei_count = _load_entities(conn, lei_zip, log)
    isin_count = 0
    if isin_zip:
        log(f"Loading ISIN mappings from {os.path.basename(isin_zip)}")
        isin_count = _load_isins(conn, isin_zip, log)

    conn.executemany("INSERT INTO meta VALUES (?,?)", [
        ("publish_date", publish_date),
        ("lei_file", os.path.basename(lei_zip)),
        ("lei_count", str(lei_count)),
        ("isin_file", os.path.basename(isin_zip) if isin_zip else ""),
        ("isin_count", str(isin_count)),
        ("loaded_at", time.strftime("%Y-%m-%d %H:%M:%S")),
    ])
    conn.commit()
    conn.close()

    os.replace(building, path)
    with open(_pointer_path() + ".tmp", "w", encoding="utf-8") as f:
        f.write(db_name)
    os.replace(_pointer_path() + ".tmp", _pointer_path())

    # Remove older copies; ones still open by a running server are left for
    # the next load to clean up.
    for old in glob.glob(os.path.join(DATA_DIR, "gleif-*.db")):
        if os.path.basename(old) != db_name:
            try:
                os.remove(old)
            except OSError:
                pass

    log(f"Done in {time.time() - started:.0f}s -> {path}")
    return path


def _download(url: str, dest: str, log) -> str:
    import requests

    if os.path.exists(dest):
        log(f"Using existing download {dest}")
        return dest
    log(f"Downloading {url}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest + ".part", "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(dest + ".part", dest)
    return dest


def download_latest(log=print) -> tuple[str, str, str]:
    """Download the latest golden copy and ISIN mapping zips.

    Returns (lei_zip_path, isin_zip_path, publish_date).
    """
    import requests

    downloads = os.path.join(DATA_DIR, "downloads")
    os.makedirs(downloads, exist_ok=True)

    publish = requests.get(GOLDEN_COPY_LATEST, timeout=30).json()["data"]
    lei_url = publish["lei2"]["full_file"]["csv"]["url"]
    lei_zip = _download(lei_url, os.path.join(downloads, lei_url.rsplit("/", 1)[-1]), log)

    isin = requests.get(ISIN_LATEST, timeout=30).json()["data"]["attributes"]
    isin_zip = _download(isin["downloadLink"], os.path.join(downloads, isin["fileName"]), log)

    # Only keep the files just used
    for old in glob.glob(os.path.join(downloads, "*.zip")):
        if old not in (lei_zip, isin_zip):
            os.remove(old)

    return lei_zip, isin_zip, publish["publish_date"]


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Manage the local GLEIF copy.")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="Download (or use given files) and build the local DB.")
    load.add_argument("--lei-zip", help="Golden copy LEI2 CSV zip (skips download).")
    load.add_argument("--isin-zip", help="ISIN-LEI mapping zip.")
    load.add_argument("--publish-date", default="", help="Publish date to record with --lei-zip.")
    sub.add_parser("status", help="Show what is loaded.")
    args = parser.parse_args(argv)

    if args.command == "status":
        for k, v in status().items():
            print(f"{k}: {v}")
        return

    if args.lei_zip:
        build(args.lei_zip, args.isin_zip, args.publish_date)
    else:
        lei_zip, isin_zip, publish_date = download_latest()
        build(lei_zip, isin_zip, publish_date)


if __name__ == "__main__":
    main(sys.argv[1:])
