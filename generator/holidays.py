"""German public holidays for bronze.ref_holiday_de (owner: DE1).

National holidays for every year, plus the most common regional ones per Bundesland. Same Easter
rule as gold.dim_date (nb_ddl_40_gold), so the calendar and the reference table agree.
"""
from __future__ import annotations

import datetime as dt

# Regional holidays (fixed or Easter-relative) -> Bundeslaender that observe them.
REGIONAL = {
    "Heilige Drei Koenige": ("fixed", (1, 6), ["BW", "BY", "ST"]),
    "Fronleichnam": ("easter", 60, ["BW", "BY", "HE", "NW", "RP", "SL"]),
    "Mariae Himmelfahrt": ("fixed", (8, 15), ["SL"]),
    "Reformationstag": ("fixed", (10, 31), ["BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"]),
    "Allerheiligen": ("fixed", (11, 1), ["BW", "BY", "NW", "RP", "SL"]),
    "Internationaler Frauentag": ("fixed", (3, 8), ["BE", "MV"]),
}


def easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    j, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * j - k - h + m + 32) % 7
    n = (h - m + r + 90) // 25
    return dt.date(y, n, (h - m + r + n + 19) % 32)


def national(y: int) -> dict[dt.date, str]:
    e = easter(y)
    return {dt.date(y, 1, 1): "Neujahr", e - dt.timedelta(2): "Karfreitag", e + dt.timedelta(1): "Ostermontag",
            dt.date(y, 5, 1): "Tag der Arbeit", e + dt.timedelta(39): "Christi Himmelfahrt", e + dt.timedelta(50): "Pfingstmontag",
            dt.date(y, 10, 3): "Tag der Deutschen Einheit", dt.date(y, 12, 25): "1. Weihnachtstag", dt.date(y, 12, 26): "2. Weihnachtstag"}


def holiday_rows(first: int = 2016, last: int = 2030) -> list[dict]:
    rows = []
    for y in range(first, last + 1):
        rows += [{"holiday_date": d, "holiday_name": n, "scope": "DE"} for d, n in national(y).items()]
        for name, (kind, when, regions) in REGIONAL.items():
            d = dt.date(y, *when) if kind == "fixed" else easter(y) + dt.timedelta(when)
            rows += [{"holiday_date": d, "holiday_name": name, "scope": r} for r in regions]
    return sorted(rows, key=lambda r: (r["holiday_date"], r["scope"]))
