from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_INF = np.inf
_EPS = 1e-9


class InfeasibleRoute(Exception):
    def __init__(self, at_mile: float, gap_miles: float):
        self.at_mile = at_mile
        super().__init__(
            f"No fuel station reachable after mile {at_mile:.0f} (next one is {gap_miles:.0f} miles away)."
        )


@dataclass(frozen=True)
class Purchase:
    index: int
    mile: float
    gallons: float
    cost: float


def plan_fuel_stops(
    miles: np.ndarray,
    prices: np.ndarray,
    total_miles: float,
    tank_range_miles: float,
    mpg: float,
    start_fuel_miles: float,
    stop_penalty: float = 0.0,
) -> list[Purchase]:
    miles = np.asarray(miles, dtype=float)
    prices = np.asarray(prices, dtype=float)
    U, F0, n = float(tank_range_miles), float(start_fuel_miles), len(miles)

    if total_miles <= F0 + _EPS:
        return []
    _check_feasible(miles, total_miles, U, F0)

    per_mile = prices / mpg
    succ_hi = np.searchsorted(miles, miles + U + _EPS, side="right")
    pred_lo = np.searchsorted(miles, miles - U - _EPS, side="left")

    g_empty = np.full(n, _INF)
    g_fill = np.full((n, n), _INF)
    g_origin = np.full(n, _INF)
    nxt_empty = np.full(n, -1)
    nxt_fill = np.full((n, n), -1)
    nxt_origin = np.full(n, -1)

    for j in range(n - 1, -1, -1):
        preds = np.arange(pred_lo[j], j)
        arrive = [np.zeros(1), U - (miles[j] - miles[preds])]
        from_origin = miles[j] <= F0 + _EPS
        if from_origin:
            arrive.append(np.array([F0 - miles[j]]))
        a = np.concatenate(arrive)[:, None]

        ks = np.arange(j + 1, succ_hi[j])
        d = (miles[ks] - miles[j])[None, :]
        cheaper_next = prices[ks] <= prices[j]
        to_station = np.where(
            cheaper_next,
            np.where(d > a + _EPS, (d - a) * per_mile[j] + g_empty[ks], _INF),
            (U - a) * per_mile[j] + g_fill[j, ks],
        )
        d_end = total_miles - miles[j]
        to_end = np.where((d_end <= U + _EPS) & (d_end > a + _EPS), (d_end - a) * per_mile[j], _INF)

        options = np.hstack([to_station, to_end])
        best = options.argmin(axis=1)
        value = options[np.arange(len(best)), best] + stop_penalty
        choice = np.where(best < len(ks), j + 1 + best, n)

        g_empty[j], nxt_empty[j] = value[0], choice[0]
        g_fill[preds, j], nxt_fill[preds, j] = value[1:1 + len(preds)], choice[1:1 + len(preds)]
        if from_origin:
            g_origin[j], nxt_origin[j] = value[-1], choice[-1]

    first = int(np.argmin(g_origin))
    if not np.isfinite(g_origin[first]):
        raise InfeasibleRoute(0.0, float(miles[0]) if n else total_miles)

    purchases: list[Purchase] = []
    j, fuel, nxt = first, F0 - miles[first], nxt_origin[first]
    while True:
        target = total_miles if nxt == n else miles[nxt]
        if nxt != n and prices[nxt] > prices[j]:
            buy = U - fuel
        else:
            buy = target - miles[j] - fuel
        if buy > _EPS:
            gallons = buy / mpg
            purchases.append(Purchase(j, float(miles[j]), gallons, gallons * float(prices[j])))
        if nxt == n:
            return purchases
        fuel = fuel + buy - (target - miles[j])
        prev, j = j, int(nxt)
        nxt = nxt_empty[j] if fuel <= _EPS else nxt_fill[prev, j]


def _check_feasible(miles: np.ndarray, total: float, U: float, F0: float) -> None:
    if not len(miles) or miles[0] > F0 + _EPS:
        raise InfeasibleRoute(0.0, float(miles[0]) if len(miles) else total)
    stops = np.append(miles, total)
    gaps = np.diff(stops)
    bad = np.flatnonzero(gaps > U + _EPS)
    if bad.size:
        raise InfeasibleRoute(float(stops[bad[0]]), float(gaps[bad[0]]))
