from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import SimpleTestCase

from planner.datasets import get_datasets
from planner.routing import Route, RoutingError
from planner.services import LocationError, resolve_location


def fake_route(*_args, **_kwargs) -> Route:
    s = get_datasets().stations
    band = np.flatnonzero((s.lat > 40.5) & (s.lat < 41.5) & (s.lon > -112) & (s.lon < -75))
    band = band[np.argsort(s.lon[band])]
    coords = np.column_stack((s.lon[band], s.lat[band]))
    return Route(coords=coords, distance_miles=2100.0, duration_hours=31.0)


class LocationTests(SimpleTestCase):
    def test_city_state_variants(self):
        for text in ("Chicago, IL", "chicago, illinois", "Chicago, IL, USA", "St. Louis, MO"):
            self.assertTrue(resolve_location(text).state, text)

    def test_lat_lon(self):
        self.assertAlmostEqual(resolve_location("40.71, -74.00").lat, 40.71)

    def test_rejects_non_us(self):
        for text in ("51.5,-0.12", "Toronto, ON", "Chicago"):
            with self.assertRaises(LocationError):
                resolve_location(text)


@mock.patch("planner.routing.fetch_route", side_effect=fake_route)
class RouteApiTests(SimpleTestCase):
    url = "/api/v1/route/"

    def setUp(self):
        cache.clear()

    def test_plan_returns_stops_cost_and_map(self, fetch):
        res = self.client.get(self.url, {"start": "Salt Lake City, UT", "finish": "New York, NY"})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertGreaterEqual(body["summary"]["num_stops"], 3)
        self.assertAlmostEqual(body["summary"]["total_fuel_cost"], sum(s["cost"] for s in body["fuel_stops"]), 1)
        kinds = {f["properties"]["kind"] for f in body["map"]["geojson"]["features"]}
        self.assertEqual(kinds, {"route", "start", "finish", "fuel_stop"})
        self.assertTrue(body["map"]["url"].startswith("/map/?"))

    def test_post_and_cache_use_single_routing_call(self, fetch):
        payload = {"start": "Salt Lake City, UT", "finish": "New York, NY", "mode": "cheapest"}
        first = self.client.post(self.url, payload, content_type="application/json").json()
        second = self.client.post(self.url, payload, content_type="application/json").json()
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(first["meta"]["cached"])
        self.assertTrue(second["meta"]["cached"])
        self.assertEqual(second["meta"]["routing_api_calls"], 0)

    def test_validation_errors(self, fetch):
        self.assertEqual(self.client.get(self.url, {"start": "Dallas, TX"}).status_code, 400)
        res = self.client.get(self.url, {"start": "Paris, France", "finish": "Dallas, TX"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "invalid_location")
        fetch.assert_not_called()

    def test_routing_failure_maps_to_502(self, fetch):
        fetch.side_effect = RoutingError("down")
        res = self.client.get(self.url, {"start": "Dallas, TX", "finish": "Austin, TX"})
        self.assertEqual(res.status_code, 502)

    def test_map_page_renders(self, fetch):
        self.assertContains(self.client.get("/map/", {"start": "Dallas, TX", "finish": "Austin, TX"}), "leaflet")
