"""GET /location/analyze — fetchers patched, no network, throwaway DB."""
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from test_helpers import client  # noqa: F401  (must import first: sets POTATO_DB)

import location
from db import connect

PLACE_OK = "पोखरा, कास्की, गण्डकी प्रदेश, नेपाल"


def _stubs(**overrides):
    """patch.multiple with a full stub fetch set — no test ever hits the network."""
    base = {
        "fetch_elevation": lambda lat, lon: 855.0,
        "fetch_climate": lambda lat, lon: {
            "annual_rain": 2800.0, "coldest_c": 11.5, "temp_c": 13.0,
        },
        "fetch_forecast_temp": lambda lat, lon: 21.2,
        "fetch_soil": lambda lat, lon: {"clay": 15.0, "sand": 60.0, "silt": 25.0, "ph": 5.8},
        "fetch_place": lambda lat, lon: PLACE_OK,
    }
    base.update(overrides)
    return patch.multiple("location", **base)


def _boom(*_args, **_kwargs):
    raise AssertionError("network fetch attempted")


class AnalyzeEndpoint(unittest.TestCase):
    def setUp(self):
        with connect() as conn:
            conn.execute("DELETE FROM location_analysis")

    def _analyze(self, **params):
        return client.get("/location/analyze", params={"lat": 28.21, "lon": 83.99, **params})

    def test_happy_path_schema(self):
        with _stubs():
            r = self._analyze()
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in ("score", "band", "place", "coords", "altitude_m", "recommendation",
                    "summary", "factors", "challenges", "rainfall_zone", "region",
                    "varieties", "tips"):
            self.assertIn(key, data)
        self.assertEqual(data["region"], "mid-hills")
        self.assertEqual([f["key"] for f in data["factors"]],
                         ["altitude", "temp", "rainfall", "soil", "climate"])
        for f in data["factors"]:
            self.assertTrue(0 <= f["score"] <= 100)
            self.assertIn("vars", f)
        self.assertEqual(data["altitude_m"], 855)
        self.assertEqual(data["place"], PLACE_OK)
        self.assertIn(data["band"], ("Excellent", "Good", "Fair", "Poor"))

    def test_result_is_cached(self):
        with _stubs():
            self.assertEqual(self._analyze().status_code, 200)
        with connect() as conn:
            row = conn.execute("SELECT payload FROM location_analysis").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("score", json.loads(row["payload"]))

    def test_cache_hit_skips_all_fetchers(self):
        with _stubs():
            self.assertEqual(self._analyze().status_code, 200)
        with _stubs(fetch_elevation=_boom, fetch_climate=_boom, fetch_forecast_temp=_boom,
                    fetch_soil=_boom, fetch_place=_boom):
            r = self._analyze()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["altitude_m"], 855)

    def test_expired_cache_refetches(self):
        stale = (datetime.utcnow() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        with connect() as conn:
            conn.execute(
                "INSERT INTO location_analysis (lat_key, lon_key, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (28.21, 83.99, json.dumps({"stale": True}), stale),
            )
        with _stubs():
            r = self._analyze()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("stale", r.json())

    def test_missing_or_bad_coords_return_400(self):
        for params in ({}, {"lat": "999", "lon": "83.99"}, {"lat": "abc", "lon": "83.99"}):
            with _stubs():
                r = client.get("/location/analyze", params=params)
            self.assertEqual(r.status_code, 400, params)
            self.assertEqual(r.json()["detail"], "Invalid coordinates")

    def test_elevation_failure_returns_502(self):
        import urllib.error

        def fail(lat, lon):
            raise urllib.error.URLError("connection refused")

        with _stubs(fetch_elevation=fail):
            r = self._analyze()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"], "Location service unavailable")

    def test_elevation_timeout_returns_504(self):
        def fail(lat, lon):
            raise TimeoutError("timed out")

        with _stubs(fetch_elevation=fail):
            r = self._analyze()
        self.assertEqual(r.status_code, 504)
        self.assertEqual(r.json()["detail"], "Location service timeout")

    def test_temp_fallback_to_forecast_when_climate_temp_missing(self):
        with _stubs(fetch_climate=lambda lat, lon: {
            "annual_rain": 2800.0, "coldest_c": 11.5, "temp_c": None,
        }):
            r = self._analyze()
        self.assertEqual(r.status_code, 200)
        temp_f = next(f for f in r.json()["factors"] if f["key"] == "temp")
        self.assertEqual(temp_f["value"], "~21°C")

    def test_both_temp_sources_fail_returns_502(self):
        def fail(lat, lon):
            raise ValueError("no temperature")

        with _stubs(
            fetch_climate=lambda lat, lon: {
                "annual_rain": 2800.0, "coldest_c": 11.5, "temp_c": None,
            },
            fetch_forecast_temp=fail,
        ):
            r = self._analyze()
        self.assertEqual(r.status_code, 502)

    def test_climate_failure_degrades_soft_sources(self):
        import urllib.error

        def fail(lat, lon):
            raise urllib.error.URLError("down")

        with _stubs(fetch_climate=fail, fetch_soil=lambda lat, lon: None,
                    fetch_place=lambda lat, lon: None):
            r = self._analyze()
        self.assertEqual(r.status_code, 200)
        data = r.json()
        rain_f = next(f for f in data["factors"] if f["key"] == "rainfall")
        self.assertEqual(rain_f["score"], 75)
        self.assertEqual(data["place"], "Your location")
        self.assertEqual(data["_meta"]["soil_source"], "zonal estimate")
        # temp must still come from the forecast fallback
        temp_f = next(f for f in data["factors"] if f["key"] == "temp")
        self.assertEqual(temp_f["value"], "~21°C")


class FetcherParsing(unittest.TestCase):
    """Unit tests for response parsing (single _get call per fetcher)."""

    def test_elevation_parse(self):
        with patch("location._get", return_value={"elevation": [855.0]}):
            self.assertEqual(location.fetch_elevation(28.2, 84.0), 855.0)

    def test_forecast_mean_skips_nulls(self):
        payload = {"daily": {"temperature_2m_mean": [21.0, 22.0, None]}}
        with patch("location._get", return_value=payload):
            self.assertEqual(location.fetch_forecast_temp(28.2, 84.0), 21.5)

    def test_forecast_all_null_raises(self):
        with patch("location._get", return_value={"daily": {"temperature_2m_mean": [None]}}):
            with self.assertRaises(ValueError):
                location.fetch_forecast_temp(28.2, 84.0)

    def test_climate_annual_rain_coldest_and_current_month(self):
        times, rains, temps = [], [], []
        for d in range(365):
            day = date(2025, 1, 1) + timedelta(days=d)
            times.append(day.isoformat())
            rains.append(2.0)
            temps.append(5.0 if day.month == 1 else 25.0)
        payload = {"daily": {"time": times, "precipitation_sum": rains,
                             "temperature_2m_mean": temps}}
        with patch("location._get", return_value=payload):
            out = location.fetch_climate(28.2, 84.0, today=date(2026, 1, 15))
        self.assertEqual(out["annual_rain"], 730.0)   # 2 mm/day × 365
        self.assertEqual(out["coldest_c"], 5.0)       # January mean
        self.assertEqual(out["temp_c"], 5.0)          # current-month normal

    def test_climate_empty_response_returns_nones(self):
        with patch("location._get", return_value={}):
            out = location.fetch_climate(28.2, 84.0)
        self.assertEqual(out, {"annual_rain": None, "coldest_c": None, "temp_c": None})

    def test_soilgrids_units_converted(self):
        def layer(name, mean):
            return {"name": name, "unit_measure": {"d_factor": 10},
                    "depths": [{"label": "0-5cm", "values": {"mean": mean}}]}
        payload = {"properties": {"layers": [
            layer("clay", 229), layer("sand", 426),
            layer("silt", 345), layer("phh2o", 62),
        ]}}
        with patch("location._get", return_value=payload):
            soil = location.fetch_soil(26.46, 87.27)
        self.assertEqual(soil, {"clay": 22.9, "sand": 42.6, "silt": 34.5, "ph": 6.2})

    def test_soilgrids_null_pixel_returns_none(self):
        def layer(name, mean):
            return {"name": name, "unit_measure": {"d_factor": 10},
                    "depths": [{"label": "0-5cm", "values": {"mean": mean}}]}
        payload = {"properties": {"layers": [
            layer("clay", None), layer("sand", None), layer("silt", None),
            layer("phh2o", None),
        ]}}
        with patch("location._get", return_value=payload):
            self.assertIsNone(location.fetch_soil(28.21, 83.99))

    def test_soilgrids_null_ph_keeps_texture(self):
        def layer(name, mean):
            return {"name": name, "unit_measure": {"d_factor": 10},
                    "depths": [{"label": "0-5cm", "values": {"mean": mean}}]}
        payload = {"properties": {"layers": [
            layer("clay", 150), layer("sand", 600), layer("silt", 250),
            layer("phh2o", None),
        ]}}
        with patch("location._get", return_value=payload):
            soil = location.fetch_soil(28.21, 83.99)
        self.assertEqual(soil, {"clay": 15.0, "sand": 60.0, "silt": 25.0, "ph": None})

    def test_place_uses_display_name(self):
        with patch("location._get", return_value={"display_name": PLACE_OK}):
            self.assertEqual(location.fetch_place(28.2, 84.0), PLACE_OK)

    def test_place_missing_returns_none(self):
        with patch("location._get", return_value={}):
            self.assertIsNone(location.fetch_place(28.2, 84.0))


if __name__ == "__main__":
    unittest.main()
