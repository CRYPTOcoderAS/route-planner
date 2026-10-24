from __future__ import annotations

import numpy as np

EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344


def to_unit_xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat_r, lon_r = np.radians(lat), np.radians(lon)
    cos_lat = np.cos(lat_r)
    return np.column_stack((cos_lat * np.cos(lon_r), cos_lat * np.sin(lon_r), np.sin(lat_r)))


def miles_to_chord(miles: float) -> float:
    return 2.0 * np.sin(miles / (2.0 * EARTH_RADIUS_MILES))


def chord_to_miles(chord: np.ndarray) -> np.ndarray:
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))


def haversine_miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def cumulative_miles(coords: np.ndarray) -> np.ndarray:
    seg = haversine_miles(coords[:-1, 1], coords[:-1, 0], coords[1:, 1], coords[1:, 0])
    return np.concatenate(([0.0], np.cumsum(seg)))


def thin_line(coords: np.ndarray, cum: np.ndarray, step_miles: float) -> np.ndarray:
    if len(coords) < 3:
        return coords
    buckets = np.floor(cum / step_miles)
    keep = np.flatnonzero(np.diff(buckets, prepend=-1) > 0)
    keep = np.union1d(keep, [len(coords) - 1])
    return coords[keep]
