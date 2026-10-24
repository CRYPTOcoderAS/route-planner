import numpy as np
from django.test import SimpleTestCase

from planner.optimizer import InfeasibleRoute, plan_fuel_stops
from planner.tests.greedy_oracle import greedy_min_cost


def plan(miles, prices, total, start=500.0, penalty=0.0):
    return plan_fuel_stops(np.array(miles, float), np.array(prices, float), total, 500.0, 10.0, start, penalty)


def assert_feasible(test, purchases, total, start=500.0):
    fuel, last = start, 0.0
    for p in purchases:
        fuel -= p.mile - last
        test.assertGreaterEqual(fuel, -1e-6, "ran dry before a stop")
        fuel += p.gallons * 10
        test.assertLessEqual(fuel, 500 + 1e-6, "overfilled the tank")
        last = p.mile
    test.assertGreaterEqual(fuel - (total - last), -1e-6, "ran dry before destination")


class OptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stop(self):
        self.assertEqual(plan([100, 200], [3.0, 3.0], 450), [])

    def test_buys_only_what_is_needed_to_finish(self):
        (p,) = plan([400], [3.0], 700)
        self.assertAlmostEqual(p.gallons, 20.0)
        self.assertAlmostEqual(p.cost, 60.0)

    def test_skips_expensive_station_for_cheaper_one_in_range(self):
        stops = plan([300, 450], [4.0, 3.0], 900)
        self.assertEqual([s.index for s in stops], [1])

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        stops = plan([100, 550, 900], [2.0, 5.0, 5.0], 1300, start=100)
        self.assertAlmostEqual(stops[0].gallons, 50.0)
        assert_feasible(self, stops, 1300, start=100)

    def test_infeasible_gap_is_reported(self):
        with self.assertRaises(InfeasibleRoute) as ctx:
            plan([100, 700], [3.0, 3.0], 900)
        self.assertEqual(ctx.exception.at_mile, 100)

    def test_matches_greedy_optimum_on_random_routes(self):
        rng = np.random.default_rng(7)
        for _ in range(200):
            total = float(rng.uniform(600, 3000))
            miles = np.sort(rng.uniform(0, total, rng.integers(20, 120)))
            if np.diff(np.concatenate(([0], miles, [total]))).max() > 480:
                continue
            prices = rng.uniform(2.7, 4.5, len(miles)).round(3)
            got = plan(miles, prices, total)
            assert_feasible(self, got, total)
            want = greedy_min_cost(miles, prices, total, 500.0, 10.0, 500.0)
            self.assertAlmostEqual(sum(p.cost for p in got), want, places=6)

    def test_stop_penalty_reduces_stops_and_stays_feasible(self):
        rng = np.random.default_rng(3)
        total = 2800.0
        miles = np.sort(rng.uniform(0, total, 300))
        prices = rng.uniform(2.8, 3.6, 300)
        cheap, balanced = plan(miles, prices, total), plan(miles, prices, total, penalty=5.0)
        assert_feasible(self, balanced, total)
        self.assertLessEqual(len(balanced), len(cheap))
        self.assertGreaterEqual(sum(p.cost for p in balanced) + 1e-9, sum(p.cost for p in cheap))
