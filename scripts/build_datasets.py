from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

import pandas as pd

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

_SUFFIX = re.compile(
    r"\s+(city and borough|(?:consolidated|metropolitan|unified) government|urban county|city|town|"
    r"township|village|borough|cdp|ccd|municipality|plantation|comunidad|zona urbana|corporation|"
    r"gore|grant|location|purchase|unorganized territory|census subarea|district)$",
    re.IGNORECASE,
)
_ABBREV = [
    (re.compile(r"\bst\.?\s"), "saint "),
    (re.compile(r"\bste\.?\s"), "sainte "),
    (re.compile(r"\bft\.?\s"), "fort "),
    (re.compile(r"\bmt\.?\s"), "mount "),
    (re.compile(r"\bpt\.?\s"), "point "),
]


def normalize(name: str, strip_suffix: bool = False) -> str:
    s = re.sub(r"\(.*?\)", "", str(name)).strip()
    if strip_suffix:
        s = _SUFFIX.sub("", s)
    s = s.lower()
    for pat, repl in _ABBREV:
        s = pat.sub(repl, s + " ").strip()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def load_gazetteer(path: Path, priority: int) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "name": df["NAME"].str.strip().str.replace(r"\s*\(balance\)", "", regex=True),
        "state": df["USPS"].str.strip(),
        "lat": df["INTPTLAT"].astype(float),
        "lon": df["INTPTLONG"].astype(float),
        "aland": df["ALAND"].astype(float),
    })
    out["key"] = out["name"].map(lambda n: normalize(n, strip_suffix=True))
    out["priority"] = priority
    hyphen = out[out["name"].str.contains("-")].assign(key=lambda d: d["name"].str.split("-").str[0].map(normalize))
    city = out[out["key"].str.endswith(" city")].assign(key=lambda d: d["key"].str.removesuffix(" city"))
    aliases = pd.concat([hyphen, city]).assign(priority=priority + 2)
    return pd.concat([out, aliases])[lambda d: d["key"] != ""]


def build_place_index(places: Path, cousubs: Path) -> pd.DataFrame:
    gaz = pd.concat([load_gazetteer(places, 0), load_gazetteer(cousubs, 1)])
    gaz = gaz.sort_values(["priority", "aland"], ascending=[True, False])
    return gaz.drop_duplicates(["key", "state"])[["key", "name", "state", "lat", "lon"]]


def geocode_stations(prices: Path, index: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_excel(prices)
    raw.columns = [c.strip() for c in raw.columns]
    df = pd.DataFrame({
        "id": raw["OPIS Truckstop ID"].astype(int),
        "name": raw["Truckstop Name"].astype(str).str.strip(),
        "address": raw["Address"].astype(str).str.strip(),
        "city": raw["City"].astype(str).str.strip(),
        "state": raw["State"].astype(str).str.strip(),
        "price": raw["Retail Price"].astype(float),
    })
    total = len(df)
    df = df[df["state"].isin(US_STATES)]
    print(f"rows: {total}, US rows: {len(df)}", file=sys.stderr)

    df = df.sort_values("price").drop_duplicates("id").sort_values("id")
    df["key"] = df["city"].map(normalize)

    lookup = {(r.key, r.state): (r.lat, r.lon) for r in index.itertuples()}
    keys_by_state: dict[str, list[str]] = {}
    for key, state in lookup:
        keys_by_state.setdefault(state, []).append(key)

    def resolve(key: str, state: str):
        hit = lookup.get((key, state))
        if hit:
            return hit
        close = difflib.get_close_matches(key, keys_by_state.get(state, []), n=1, cutoff=0.88)
        return lookup[(close[0], state)] if close else (None, None)

    coords = [resolve(k, s) for k, s in zip(df["key"], df["state"])]
    df["lat"] = [c[0] for c in coords]
    df["lon"] = [c[1] for c in coords]

    missing = df[df["lat"].isna()]
    print(f"unique stations: {len(df)}, geocoded: {len(df) - len(missing)}, "
          f"unmatched: {len(missing)}", file=sys.stderr)
    if len(missing):
        print("sample unmatched:", missing[["city", "state"]].head(15).values.tolist(), file=sys.stderr)
    return df.dropna(subset=["lat"]).drop(columns="key")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", type=Path, required=True)
    ap.add_argument("--places", type=Path, required=True)
    ap.add_argument("--cousubs", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    args = ap.parse_args()

    index = build_place_index(args.places, args.cousubs)
    stations = geocode_stations(args.prices, index)

    args.out.mkdir(parents=True, exist_ok=True)
    stations.round({"lat": 5, "lon": 5, "price": 4}).to_csv(args.out / "stations.csv", index=False)
    places = index[index["state"].isin(US_STATES)].round({"lat": 5, "lon": 5})
    places[["key", "name", "state", "lat", "lon"]].to_csv(args.out / "us_places.csv", index=False)
    print(f"wrote {len(stations)} stations, {len(places)} places -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
