from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import numpy as np
from django.conf import settings
from django.core.cache import cache
from scipy.spatial import cKDTree

from planner import routing
from planner.datasets import STATE_CODES, STATE_NAMES, Place, get_datasets
from planner.geo import chord_to_miles, cumulative_miles, miles_to_chord, thin_line, to_unit_xyz
from planner.optimizer import plan_fuel_stops

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_SAMPLE_STEP_MILES = 0.5
_GEOMETRY_STEP_MILES = 1.0

_US_BOXES = ((24.4, 49.5, -125.0, -66.9), (51.0, 71.5, -179.2, -129.9), (18.9, 22.3, -160.3, -154.8))


class LocationError(ValueError):
    pass


@dataclass(frozen=True)
class TripRequest:
    start: str
    finish: str
    start_fuel_gallons: float | None = None
    mode: str = "balanced"


def resolve_location(text: str) -> Place:
    if m := _LATLON.match(text):
        lat, lon = float(m[1]), float(m[2])
        if not any(a <= lat <= b and c <= lon <= d for a, b, c, d in _US_BOXES):
            raise LocationError(f"'{text}' is outside the USA.")
        return Place(f"{lat:.4f},{lon:.4f}", "", lat, lon)

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts and parts[-1].lower() in {"usa", "us", "united states", "united states of america"}:
        parts = parts[:-1]
    if len(parts) < 2:
        raise LocationError(f"'{text}': use 'City, ST' (e.g. 'Dallas, TX') or 'lat,lon'.")

    city, state_raw = ", ".join(parts[:-1]), parts[-1]
    state = state_raw.upper() if state_raw.upper() in STATE_CODES else STATE_NAMES.get(state_raw.lower())
    if not state:
        raise LocationError(f"'{state_raw}' is not a US state.")
    place = get_datasets().places.lookup(city, state)
    if place is None:
        raise LocationError(f"Could not find '{city}, {state}'. Try a nearby city or 'lat,lon'.")
    return place


def _stations_along_route(coords: np.ndarray, cum: np.ndarray, corridor_miles: float):
    stations = get_datasets().stations

    pad = corridor_miles / 45.0
    lon_min, lat_min = coords.min(axis=0) - pad
    lon_max, lat_max = coords.max(axis=0) + pad
    box = np.flatnonzero(
        (stations.lat >= lat_min) & (stations.lat <= lat_max) & (stations.lon >= lon_min) & (stations.lon <= lon_max)
    )
    if box.size == 0:
        return box, np.empty(0), np.empty(0)

    samples = np.append(np.arange(0.0, cum[-1], _SAMPLE_STEP_MILES), cum[-1])
    lon_s = np.interp(samples, cum, coords[:, 0])
    lat_s = np.interp(samples, cum, coords[:, 1])

    tree = cKDTree(to_unit_xyz(lat_s, lon_s))
    dist, idx = tree.query(stations.xyz[box], distance_upper_bound=miles_to_chord(corridor_miles))
    hit = np.isfinite(dist)
    return box[hit], samples[idx[hit]], chord_to_miles(dist[hit])


def _cache_key(start: Place, finish: Place, start_fuel: float, mode: str) -> str:
    raw = f"{start.lat:.4f},{start.lon:.4f}|{finish.lat:.4f},{finish.lon:.4f}|{start_fuel:.2f}|{mode}"
    return "plan:" + hashlib.sha1(raw.encode()).hexdigest()


def plan_trip(req: TripRequest) -> dict:
    t0 = time.perf_counter()
    cfg = settings.FUEL_PLANNER
    tank_range, mpg = cfg["VEHICLE_RANGE_MILES"], cfg["VEHICLE_MPG"]
    tank_gallons = tank_range / mpg
    start_fuel = tank_gallons if req.start_fuel_gallons is None else min(req.start_fuel_gallons, tank_gallons)

    start, finish = resolve_location(req.start), resolve_location(req.finish)
    key = _cache_key(start, finish, start_fuel, req.mode)
    if (hit := cache.get(key)) is not None:
        hit["meta"] = {**hit["meta"], "cached": True, "routing_api_calls": 0,
                       "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
        return hit

    t_route = time.perf_counter()
    route = routing.fetch_route((start.lat, start.lon), (finish.lat, finish.lon))
    routing_ms = (time.perf_counter() - t_route) * 1000
    cum = cumulative_miles(route.coords)
    cum *= route.distance_miles / cum[-1] if cum[-1] > 0 else 1.0

    st_idx, st_mile, st_off = _stations_along_route(route.coords, cum, cfg["CORRIDOR_MILES"])
    stations = get_datasets().stations
    order = np.lexsort((stations.prices[st_idx], st_mile))
    st_idx, st_mile, st_off = st_idx[order], st_mile[order], st_off[order]
    first = np.flatnonzero(np.diff(st_mile, prepend=-1.0) > 0)
    cand_idx, cand_mile, cand_off = st_idx[first], st_mile[first], st_off[first]

    purchases = plan_fuel_stops(
        miles=cand_mile,
        prices=stations.prices[cand_idx],
        total_miles=route.distance_miles,
        tank_range_miles=tank_range,
        mpg=mpg,
        start_fuel_miles=start_fuel * mpg,
        stop_penalty=cfg["STOP_PENALTY_USD"] if req.mode == "balanced" else 0.0,
    )

    stops = []
    for n, p in enumerate(purchases, start=1):
        i = cand_idx[p.index]
        stops.append({
            "stop": n,
            "mile_marker": round(p.mile, 1),
            "off_route_miles": round(float(cand_off[p.index]), 1),
            "gallons": round(p.gallons, 2),
            "cost": round(p.cost, 2),
            "station": stations.describe(i),
        })

    gallons_bought = sum(p.gallons for p in purchases)
    spent = sum(p.cost for p in purchases)
    gallons_used = route.distance_miles / mpg
    corridor_prices = stations.prices[st_idx]
    avg_paid = spent / gallons_bought if gallons_bought else (
        float(corridor_prices.mean()) if corridor_prices.size else None)

    result = {
        "start": _place_json(req.start, start),
        "finish": _place_json(req.finish, finish),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
        },
        "mode": req.mode,
        "vehicle": {
            "range_miles": tank_range,
            "mpg": mpg,
            "tank_gallons": tank_gallons,
            "start_fuel_gallons": round(start_fuel, 2),
        },
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost": round(spent, 2),
            "gallons_purchased": round(gallons_bought, 2),
            "gallons_used": round(gallons_used, 2),
            "trip_fuel_cost": round(gallons_used * avg_paid, 2) if avg_paid else None,
            "avg_price_paid": round(avg_paid, 3) if avg_paid else None,
            "num_stops": len(stops),
            "stations_on_route": int(st_idx.size),
            "cheapest_on_route": round(float(corridor_prices.min()), 3) if corridor_prices.size else None,
            "priciest_on_route": round(float(corridor_prices.max()), 3) if corridor_prices.size else None,
        },
        "map": {
            "url": "/map/?" + urlencode({"start": req.start, "finish": req.finish}),
            "geojson": _geojson(route.coords, cum, start, finish, stops),
        },
        "assumptions": [
            f"Vehicle: {tank_range:.0f} mi range, {mpg:.0f} MPG ({tank_gallons:.0f} gal tank), "
            f"departs with {start_fuel:.0f} gal.",
            "total_fuel_cost = money spent at the recommended stops; trip_fuel_cost = all fuel "
            "burned on the trip valued at the average price paid.",
            "mode=balanced minimises fuel cost + $%.0f per stop; mode=cheapest minimises fuel cost only."
            % cfg["STOP_PENALTY_USD"],
            f"Stations within {cfg['CORRIDOR_MILES']:.0f} mi of the route are eligible.",
        ],
        "meta": {"cached": False, "routing_api_calls": 1},
    }
    cache.set(key, result)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    result["meta"].update(
        elapsed_ms=round(elapsed_ms, 1),
        routing_ms=round(routing_ms, 1),
        compute_ms=round(elapsed_ms - routing_ms, 1),
    )
    return result


def _place_json(query: str, p: Place) -> dict:
    return {"query": query, "resolved": p.label if p.state else p.name, "lat": p.lat, "lon": p.lon}


def _geojson(coords: np.ndarray, cum: np.ndarray, start: Place, finish: Place, stops: list[dict]) -> dict:
    line = np.round(thin_line(coords, cum, _GEOMETRY_STEP_MILES), 5).tolist()
    features = [
        {"type": "Feature", "properties": {"kind": "route"}, "geometry": {"type": "LineString", "coordinates": line}},
        _point(start.lon, start.lat, {"kind": "start", "name": start.label if start.state else start.name}),
        _point(finish.lon, finish.lat, {"kind": "finish", "name": finish.label if finish.state else finish.name}),
    ]
    for s in stops:
        st = s["station"]
        features.append(_point(st["lon"], st["lat"], {
            "kind": "fuel_stop", "stop": s["stop"], "name": st["name"], "city": f"{st['city']}, {st['state']}",
            "price_per_gallon": st["price_per_gallon"], "gallons": s["gallons"], "cost": s["cost"],
            "mile_marker": s["mile_marker"],
        }))
    return {"type": "FeatureCollection", "features": features}


def _point(lon: float, lat: float, props: dict) -> dict:
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lon, lat]}}
