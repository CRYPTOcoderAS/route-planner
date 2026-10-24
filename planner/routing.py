from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from planner.geo import METERS_PER_MILE

log = logging.getLogger(__name__)


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    coords: np.ndarray
    distance_miles: float
    duration_hours: float


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=(429, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=16))
    s.headers["User-Agent"] = "fuel-route-planner/1.0 (assessment demo)"
    return s


_http = _session()


def fetch_route(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    key = "osrm:{:.4f},{:.4f}:{:.4f},{:.4f}".format(*start, *finish)
    cached = cache.get(key)
    if cached is not None:
        return cached

    cfg = settings.FUEL_PLANNER
    url = "{base}/route/v1/driving/{a_lon},{a_lat};{b_lon},{b_lat}".format(
        base=cfg["OSRM_URL"].rstrip("/"), a_lat=start[0], a_lon=start[1], b_lat=finish[0], b_lon=finish[1]
    )
    params = {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}
    try:
        resp = _http.get(url, params=params, timeout=cfg["OSRM_TIMEOUT_S"])
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("OSRM request failed: %s", exc)
        raise RoutingError("Routing service is unavailable, please retry shortly.") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(payload.get("message") or "No drivable route found between these locations.")

    best = payload["routes"][0]
    route = Route(
        coords=np.asarray(best["geometry"]["coordinates"], dtype=float),
        distance_miles=best["distance"] / METERS_PER_MILE,
        duration_hours=best["duration"] / 3600.0,
    )
    cache.set(key, route)
    return route
