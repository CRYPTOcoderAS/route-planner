import numpy as np


def greedy_min_cost(miles, prices, total, U, mpg, F0) -> float:
    n = len(miles)
    pos = np.append(miles, total)
    costs = np.append(prices, -np.inf)
    cur, cur_mile, fuel, spent = -1, 0.0, F0, 0.0
    while cur != n:
        at_station = cur >= 0
        reach = cur_mile + (U if at_station else fuel)
        lo, hi = cur + 1, int(np.searchsorted(pos, reach + 1e-9, side="right"))
        window = costs[lo:hi]
        here = prices[cur] if at_station else np.inf
        cheaper = np.flatnonzero(window < here)
        if cheaper.size:
            nxt, needed = lo + int(cheaper[0]), pos[lo + int(cheaper[0])] - cur_mile
        else:
            nxt, needed = lo + int(np.argmin(window)), U
        if at_station:
            buy = max(0.0, needed - fuel)
            spent += buy / mpg * prices[cur]
            fuel += buy
        fuel -= pos[nxt] - cur_mile
        cur, cur_mile = nxt, pos[nxt]
    return spent
