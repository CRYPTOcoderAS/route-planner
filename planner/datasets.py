from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from django.conf import settings

from planner.geo import to_unit_xyz

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_CODES = set(STATE_NAMES.values())


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


class StationIndex:

    def __init__(self, rows: list[dict]):
        self.ids = np.array([int(r["id"]) for r in rows])
        self.names = [r["name"] for r in rows]
        self.addresses = [r["address"] for r in rows]
        self.cities = [r["city"] for r in rows]
        self.states = [r["state"] for r in rows]
        self.prices = np.array([float(r["price"]) for r in rows])
        self.lat = np.array([float(r["lat"]) for r in rows])
        self.lon = np.array([float(r["lon"]) for r in rows])
        self.xyz = to_unit_xyz(self.lat, self.lon)

    def __len__(self) -> int:
        return len(self.ids)

    def describe(self, i: int) -> dict:
        return {
            "id": int(self.ids[i]),
            "name": self.names[i],
            "address": self.addresses[i],
            "city": self.cities[i],
            "state": self.states[i],
            "lat": float(self.lat[i]),
            "lon": float(self.lon[i]),
            "price_per_gallon": round(float(self.prices[i]), 3),
        }


class PlaceIndex:

    _ABBREV = [(r"\bst\.?\s", "saint "), (r"\bste\.?\s", "sainte "), (r"\bft\.?\s", "fort "),
               (r"\bmt\.?\s", "mount "), (r"\bpt\.?\s", "point ")]

    def __init__(self, rows: list[dict]):
        self._by_key = {(r["key"], r["state"]): Place(r["name"], r["state"], float(r["lat"]), float(r["lon"])) for r in rows}
        rows = sorted(rows, key=lambda r: r["key"])
        self._keys = np.array([r["key"] for r in rows])
        self._labels = [r["label"] for r in rows]
        self._states = np.array([r["state"] for r in rows])
        self._lat = np.array([float(r["lat"]) for r in rows])
        self._lon = np.array([float(r["lon"]) for r in rows])
        self._score = np.array([int(r["score"]) for r in rows], dtype=np.int64)

    @classmethod
    def normalize(cls, name: str) -> str:
        s = name.lower().strip() + " "
        for pat, repl in cls._ABBREV:
            s = re.sub(pat, repl, s)
        s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
        return re.sub(r"\s+", " ", s).strip()

    def lookup(self, city: str, state: str) -> Place | None:
        return self._by_key.get((self.normalize(city), state))

    def suggest(self, query: str, limit: int = 8) -> list[dict]:
        city, _, state_part = query.partition(",")
        prefix = self.normalize(city)
        if not prefix:
            return []
        lo = int(np.searchsorted(self._keys, prefix, side="left"))
        hi = int(np.searchsorted(self._keys, prefix + "￿", side="left"))
        idx = np.arange(lo, hi)
        state_part = state_part.strip()
        if state_part and idx.size:
            codes = {code for name, code in STATE_NAMES.items() if name.startswith(state_part.lower())}
            codes |= {c for c in STATE_CODES if c.startswith(state_part.upper())}
            idx = idx[np.isin(self._states[idx], list(codes))]
        idx = idx[np.argsort(-self._score[idx], kind="stable")]

        results, seen = [], set()
        for i in idx:
            spot = (round(self._lat[i], 3), round(self._lon[i], 3))
            if spot in seen:
                continue
            seen.add(spot)
            label = f"{self._labels[i]}, {self._states[i]}"
            results.append({"label": label, "city": self._labels[i], "state": str(self._states[i]),
                            "lat": float(self._lat[i]), "lon": float(self._lon[i])})
            if len(results) == limit:
                break
        return results


@dataclass(frozen=True)
class Datasets:
    stations: StationIndex
    places: PlaceIndex


def _read_csv(name: str) -> list[dict]:
    path = settings.FUEL_PLANNER["DATA_DIR"] / name
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def get_datasets() -> Datasets:
    return Datasets(
        stations=StationIndex(_read_csv("stations.csv")),
        places=PlaceIndex(_read_csv("us_places.csv")),
    )
