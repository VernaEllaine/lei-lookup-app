"""Map ISO 3166-1 alpha-2 country codes to a reporting region."""

from __future__ import annotations

# Geographic Europe: EEA (EU27 + IS, LI, NO), the UK, Switzerland, the Crown
# Dependencies, European microstates and dependencies, the Western Balkans,
# Moldova, Ukraine and Belarus. Russia and Turkey are classed as Global.
EUROPE = frozenset({
    # EU27
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
    # Rest of the EEA
    "IS", "LI", "NO",
    # UK, Switzerland, Crown Dependencies, Gibraltar
    "GB", "CH", "JE", "GG", "IM", "GI",
    # Microstates and dependencies (Faroe Islands, Aland)
    "MC", "AD", "SM", "VA", "FO", "AX",
    # Western Balkans
    "AL", "BA", "XK", "ME", "MK", "RS",
    # Eastern Europe
    "MD", "UA", "BY",
})


def region_for(country: str) -> str:
    """Return "Europe" or "Global" for an ISO alpha-2 code ("" if unknown)."""
    code = (country or "").strip().upper()
    if not code:
        return ""
    return "Europe" if code in EUROPE else "Global"
