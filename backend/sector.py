"""Classify issuers into sectors from GLEIF data.

Rules (first match wins):

0. Curated list: an LEI listed in backend/data/sector_overrides.csv gets the
   sector given there. Use it for issuers the rules below miss or get wrong,
   e.g. banking groups whose names have no bank keyword (ING Groep, UBS).
1. SPS (sovereigns and public sector): EntityCategory INTERNATIONAL_ORGANIZATION
   or RESIDENT_GOVERNMENT_ENTITY (any EntitySubCategory: central, state or
   local government, social security, or none given).
2. Banks: EntityCategory GENERAL and the legal or English name contains
   "Bank", "Banco", "Banque", "Banca", "Caja", "Caixa", "Sparkasse",
   "Credit Union" or "Savings Bank", and isn't a foundation (no "Stiftung",
   "Fundación", "Fundació", "Fondazione" or "Foundation" in the name).
3. Project Finance: EntityCategory GENERAL and the name contains the word
   "Project", "Solar", "Wind", "Financing", "Infrastructure" or "PPP".
4. Utilities: EntityCategory GENERAL and the name contains the word "Power",
   "Water", "Gas", "Electric", "Grid" or "Energy Distribution".

Anything else is "Other". A BIC registered to the LEI (GLEIF LEI-BIC
mapping) is reported in the basis as supporting evidence, but doesn't make an
entity a bank on its own: most BIC holders are corporates, not banks.
"""

from __future__ import annotations

import csv
import logging
import os
import re

SPS = "SPS"
BANK = "Bank"
PROJECT_FINANCE = "Project Finance"
UTILITIES = "Utilities"
OTHER = "Other"
SECTORS = (SPS, BANK, PROJECT_FINANCE, UTILITIES, OTHER)

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "data", "sector_overrides.csv")

log = logging.getLogger(__name__)

# Bank terms match anywhere in a word so compounds like "Landesbank" or
# "Kreissparkasse" count, except "Caja" (Spanish for "box" too), which must be
# a whole word; the other lists match whole words only.
_BANK = re.compile(r"bank|banco|banque|banca|\bcaja\b|caixa|sparkasse|credit union",
                   re.IGNORECASE)
# Bank-sponsored charities ("Sparkassenstiftung", "Fundación Ibercaja")
_FOUNDATION = re.compile(r"stiftung|fundaci[oó]n?|fondazione|foundation", re.IGNORECASE)
_PROJECT = re.compile(
    r"\b(projects?|solar|wind|financing|infrastructure|ppp)\b", re.IGNORECASE)
_UTILITY = re.compile(
    r"\b(power|water|gas|electric|grid|energy distribution)\b", re.IGNORECASE)


_overrides: dict[str, str] = {}
_overrides_mtime: float | None = None


def overrides() -> dict[str, str]:
    """LEI -> sector from the curated list, reloaded when the file changes.

    Rows with an unknown sector or a malformed LEI are skipped with a warning.
    """
    global _overrides, _overrides_mtime
    try:
        mtime = os.path.getmtime(OVERRIDES_PATH)
    except OSError:
        _overrides, _overrides_mtime = {}, None
        return _overrides
    if mtime != _overrides_mtime:
        loaded: dict[str, str] = {}
        with open(OVERRIDES_PATH, encoding="utf-8-sig", newline="") as f:
            for n, row in enumerate(csv.DictReader(f), start=2):
                lei = (row.get("LEI") or "").strip().upper()
                sector = (row.get("Sector") or "").strip()
                if not re.fullmatch(r"[A-Z0-9]{18}[0-9]{2}", lei) or sector not in SECTORS:
                    log.warning("%s line %d skipped: %r", OVERRIDES_PATH, n, row)
                    continue
                loaded[lei] = sector
        _overrides, _overrides_mtime = loaded, mtime
    return _overrides


def classify(category: str, subcategory: str, names: list[str],
             has_bic: bool = False, lei: str = "") -> tuple[str, str]:
    """Return (sector, basis) for one entity.

    *names* are the legal name and any English name; *basis* explains which
    rule matched, for display and export.
    """
    override = overrides().get((lei or "").strip().upper())
    if override:
        return override, "Curated list"

    category = (category or "").upper()
    subcategory = (subcategory or "").upper()
    text = " | ".join(n for n in names if n)
    bic_note = "; BIC registered" if has_bic else ""

    basis = f"Category: {category or 'unknown'}" + (f" / {subcategory}" if subcategory else "")

    if category in ("INTERNATIONAL_ORGANIZATION", "RESIDENT_GOVERNMENT_ENTITY"):
        return SPS, basis

    if category == "GENERAL":
        for sector, pattern in ((BANK, _BANK), (PROJECT_FINANCE, _PROJECT),
                                (UTILITIES, _UTILITY)):
            m = pattern.search(text)
            if m and sector == BANK and _FOUNDATION.search(text):
                continue
            if m:
                return sector, f'Name contains "{m.group(0)}"{bic_note}'

    return OTHER, basis + bic_note
