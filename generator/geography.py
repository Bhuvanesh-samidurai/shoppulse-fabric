"""German geography used to relocate Olist (owner: DE1).

Cities are real (name, Bundesland, approximate population and coordinates). Postcodes are
synthetic: real 5-digit PLZ drawn inside each city's postcode range, so they look German and
join consistently, but a given PLZ is not guaranteed to exist. The README states this.
"""
from __future__ import annotations

import hashlib

REGIONS = {  # ISO 3166-2:DE suffix -> name, macro region, warehouse (SPEC §2: HAM, DOR, MUC)
    "SH": ("Schleswig-Holstein", "Nord", "HAM"), "HH": ("Hamburg", "Nord", "HAM"), "HB": ("Bremen", "Nord", "HAM"),
    "NI": ("Niedersachsen", "Nord", "HAM"), "MV": ("Mecklenburg-Vorpommern", "Nord", "HAM"),
    "BE": ("Berlin", "Ost", "HAM"), "BB": ("Brandenburg", "Ost", "HAM"),
    "SN": ("Sachsen", "Ost", "MUC"), "ST": ("Sachsen-Anhalt", "Ost", "MUC"), "TH": ("Thueringen", "Ost", "MUC"),
    "NW": ("Nordrhein-Westfalen", "West", "DOR"), "HE": ("Hessen", "West", "DOR"),
    "RP": ("Rheinland-Pfalz", "West", "DOR"), "SL": ("Saarland", "West", "DOR"),
    "BW": ("Baden-Wuerttemberg", "Sued", "MUC"), "BY": ("Bayern", "Sued", "MUC"),
}

# (city, region, plz_from, plz_to, population in thousands, lat, lon)
CITIES = [
    ("Berlin", "BE", 10115, 14199, 3850, 52.52, 13.40), ("Hamburg", "HH", 20095, 22769, 1900, 53.55, 9.99),
    ("Muenchen", "BY", 80331, 81929, 1510, 48.14, 11.58), ("Koeln", "NW", 50667, 51149, 1085, 50.94, 6.96),
    ("Frankfurt am Main", "HE", 60306, 60599, 775, 50.11, 8.68), ("Stuttgart", "BW", 70173, 70629, 635, 48.78, 9.18),
    ("Duesseldorf", "NW", 40210, 40629, 630, 51.23, 6.78), ("Leipzig", "SN", 4103, 4357, 620, 51.34, 12.37),
    ("Dortmund", "NW", 44135, 44388, 595, 51.51, 7.47), ("Essen", "NW", 45127, 45359, 585, 51.46, 7.01),
    ("Bremen", "HB", 28195, 28779, 570, 53.08, 8.80), ("Dresden", "SN", 1067, 1328, 565, 51.05, 13.74),
    ("Hannover", "NI", 30159, 30669, 550, 52.38, 9.73), ("Nuernberg", "BY", 90402, 90491, 525, 49.45, 11.08),
    ("Duisburg", "NW", 47051, 47279, 500, 51.43, 6.76), ("Bochum", "NW", 44787, 44894, 365, 51.48, 7.22),
    ("Wuppertal", "NW", 42103, 42399, 355, 51.26, 7.15), ("Bielefeld", "NW", 33602, 33739, 335, 52.02, 8.53),
    ("Bonn", "NW", 53111, 53229, 335, 50.73, 7.10), ("Muenster", "NW", 48143, 48167, 320, 51.96, 7.63),
    ("Mannheim", "BW", 68159, 68309, 315, 49.49, 8.47), ("Karlsruhe", "BW", 76131, 76229, 310, 49.01, 8.40),
    ("Augsburg", "BY", 86150, 86199, 300, 48.37, 10.90), ("Wiesbaden", "HE", 65183, 65207, 280, 50.08, 8.24),
    ("Moenchengladbach", "NW", 41061, 41239, 260, 51.19, 6.44), ("Gelsenkirchen", "NW", 45879, 45899, 260, 51.51, 7.10),
    ("Aachen", "NW", 52062, 52080, 250, 50.78, 6.08), ("Braunschweig", "NI", 38100, 38126, 250, 52.27, 10.52),
    ("Kiel", "SH", 24103, 24159, 245, 54.32, 10.14), ("Chemnitz", "SN", 9111, 9247, 245, 50.83, 12.92),
    ("Halle (Saale)", "ST", 6108, 6132, 240, 51.48, 11.97), ("Magdeburg", "ST", 39104, 39130, 237, 52.12, 11.63),
    ("Freiburg im Breisgau", "BW", 79098, 79117, 230, 47.99, 7.84), ("Krefeld", "NW", 47798, 47839, 227, 51.33, 6.56),
    ("Mainz", "RP", 55116, 55131, 220, 49.99, 8.25), ("Luebeck", "SH", 23552, 23570, 217, 53.87, 10.69),
    ("Erfurt", "TH", 99084, 99099, 214, 50.98, 11.03), ("Rostock", "MV", 18055, 18147, 209, 54.09, 12.10),
    ("Kassel", "HE", 34117, 34134, 200, 51.31, 9.48), ("Potsdam", "BB", 14467, 14482, 185, 52.39, 13.06),
    ("Saarbruecken", "SL", 66111, 66133, 180, 49.23, 7.00), ("Ludwigshafen am Rhein", "RP", 67059, 67071, 172, 49.48, 8.44),
    ("Oldenburg", "NI", 26121, 26135, 170, 53.14, 8.21), ("Osnabrueck", "NI", 49074, 49090, 165, 52.28, 8.05),
    ("Heidelberg", "BW", 69115, 69126, 160, 49.40, 8.69), ("Darmstadt", "HE", 64283, 64297, 160, 49.87, 8.65),
    ("Regensburg", "BY", 93047, 93059, 153, 49.01, 12.10), ("Ingolstadt", "BY", 85049, 85057, 138, 48.77, 11.43),
    ("Wuerzburg", "BY", 97070, 97084, 127, 49.79, 9.95), ("Ulm", "BW", 89073, 89081, 126, 48.40, 9.99),
    ("Goettingen", "NI", 37073, 37085, 118, 51.54, 9.92), ("Bremerhaven", "HB", 27568, 27580, 113, 53.54, 8.58),
    ("Trier", "RP", 54290, 54296, 110, 49.75, 6.64), ("Jena", "TH", 7743, 7751, 110, 50.93, 11.59),
    ("Cottbus", "BB", 3042, 3055, 100, 51.76, 14.33), ("Kaiserslautern", "RP", 67655, 67663, 100, 49.44, 7.77),
    ("Schwerin", "MV", 19053, 19063, 95, 53.63, 11.41), ("Flensburg", "SH", 24937, 24944, 90, 54.79, 9.44),
]

# Warehouse sites (silver.warehouse seed in nb_ddl_30_silver uses the same PLZ).
WAREHOUSE_SITES = [
    ("21129", "Hamburg", "HH", 53.53, 9.86), ("44147", "Dortmund", "NW", 51.53, 7.44), ("85748", "Garching bei Muenchen", "BY", 48.25, 11.65),
]

_TOTAL = sum(c[4] for c in CITIES)
_CUMULATIVE = []
_acc = 0
for _c in CITIES:
    _acc += _c[4]
    _CUMULATIVE.append(_acc)


def _h(key: str, salt: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{salt}|{key}".encode()).digest()[:8], "big")


def city_for(key: str) -> tuple:
    """Deterministic, population-weighted German city for any key (e.g. an Olist zip prefix)."""
    x = _h(key, "city") % _TOTAL
    for c, upper in zip(CITIES, _CUMULATIVE, strict=True):
        if x < upper:
            return c
    return CITIES[-1]


def place_for(key: str) -> dict:
    """City, Bundesland and a synthetic PLZ inside the city's range, deterministic per key."""
    name, region, lo, hi, _, lat, lon = city_for(key)
    plz = lo + _h(key, "plz") % (hi - lo + 1)
    return {"postal_code": f"{plz:05d}", "city": name, "region_code": region, "lat": lat, "lon": lon}


def warehouse_for(region_code: str) -> str:
    return REGIONS[region_code][2]
