"""location_rules.py — Nepal-grounded pure suitability rules (no I/O)."""
import unittest

from location_rules import (
    altitude_zone,
    altitude_score,
    band_of,
    build_analysis,
    challenges,
    climate_zone,
    feasible_seasons,
    moisture_class,
    overall_score,
    ph_score,
    rainfall_score,
    recommend_varieties,
    region_of,
    soil_score,
    temperature_score,
    texture_class,
    tips,
)

OK_SOIL = {"clay": 15.0, "sand": 60.0, "silt": 25.0, "ph": 5.8}


class ZoneAndScoreTest(unittest.TestCase):
    def test_altitude_zones_follow_nepal_classification(self):
        self.assertEqual(altitude_zone(120), "Tropical")
        self.assertEqual(altitude_zone(855), "Sub-tropical")
        self.assertEqual(altitude_zone(1500), "Warm Temperate")
        self.assertEqual(altitude_zone(2400), "Cool Temperate")
        self.assertEqual(altitude_zone(3400), "Sub-alpine")
        self.assertEqual(altitude_zone(4300), "Alpine")
        self.assertEqual(region_of(150), "Terai")
        self.assertEqual(region_of(811), "mid-hills")
        self.assertEqual(region_of(1500), "mid-hills")
        self.assertEqual(region_of(2400), "high hills")

    def test_altitude_score_peaks_in_800_3000_and_falls_off(self):
        self.assertEqual(altitude_score(1500), 100)
        self.assertEqual(altitude_score(800), 100)
        self.assertEqual(altitude_score(3000), 100)
        self.assertLess(altitude_score(200), 70)
        self.assertLess(altitude_score(3800), 60)
        self.assertEqual(altitude_score(4600), 0)
        # linear ramp between breakpoints (round-half-to-even)
        self.assertEqual(altitude_score(600), 82)

    def test_temperature_score_ideal_8_20(self):
        self.assertEqual(temperature_score(13), 100)
        self.assertEqual(temperature_score(6), 80)
        self.assertEqual(temperature_score(22), 80)
        self.assertEqual(temperature_score(3), 60)
        self.assertEqual(temperature_score(24), 60)
        self.assertEqual(temperature_score(30), 25)
        self.assertEqual(temperature_score(-2), 25)

    def test_rainfall_score_and_moisture_classes(self):
        self.assertEqual(rainfall_score(1200), 100)
        self.assertEqual(rainfall_score(500), 80)
        self.assertEqual(rainfall_score(2500), 80)
        self.assertEqual(rainfall_score(3000), 60)
        self.assertEqual(rainfall_score(100), 30)
        self.assertEqual(rainfall_score(None), 75)
        self.assertEqual(moisture_class(300), "Arid")
        self.assertEqual(moisture_class(700), "Semi-arid")
        self.assertEqual(moisture_class(1200), "Sub-humid")
        self.assertEqual(moisture_class(1800), "Humid")
        self.assertEqual(moisture_class(2600), "Per-humid")
        self.assertIsNone(moisture_class(None))

    def test_climate_zone_from_coldest_month(self):
        self.assertEqual(climate_zone(16), "Tropical")
        self.assertEqual(climate_zone(10), "Subtropical")
        self.assertEqual(climate_zone(5), "Temperate")
        self.assertEqual(climate_zone(1), "Cold")
        self.assertEqual(climate_zone(-4), "Alpine")


class SoilTest(unittest.TestCase):
    def test_usda_texture_box_rules(self):
        self.assertEqual(texture_class(5, 92, 3), "Sand")
        self.assertEqual(texture_class(8, 65, 27), "Sandy Loam")
        self.assertEqual(texture_class(18, 38, 44), "Loam")
        self.assertEqual(texture_class(12, 20, 68), "Silt Loam")
        self.assertEqual(texture_class(30, 25, 45), "Clay Loam")
        self.assertEqual(texture_class(30, 55, 15), "Sandy Clay Loam")
        self.assertEqual(texture_class(45, 35, 20), "Clay")
        self.assertEqual(texture_class(45, 10, 45), "Silty Clay")
        self.assertEqual(texture_class(10, 80, 10), "Loamy Sand")

    def test_ph_score_and_combined_soil_score(self):
        self.assertEqual(ph_score(5.8), 100)
        self.assertEqual(ph_score(5.2), 85)
        self.assertEqual(ph_score(4.8), 65)
        self.assertEqual(ph_score(7.8), 40)
        self.assertEqual(ph_score(None), 75)
        self.assertEqual(soil_score("Loam", 5.8), 95)
        self.assertEqual(soil_score("Sand", 4.8), round(0.6 * 70 + 0.4 * 65))
        self.assertEqual(soil_score("Clay", None), round(0.6 * 55 + 0.4 * 75))


class SeasonVarietyTextTest(unittest.TestCase):
    def test_seasons_by_altitude(self):
        self.assertEqual(feasible_seasons(855), "Winter (Oct–Feb) & Spring (Mar–May)")
        self.assertEqual(feasible_seasons(120), "Winter (Oct–Feb)")
        self.assertEqual(feasible_seasons(3500), "Off-season")

    def test_varieties_filtered_and_ranked_by_altitude(self):
        terai = recommend_varieties(150)
        self.assertIn("Khumal Rato-2", terai)
        self.assertNotIn("Rojita", terai)  # band 1600–3500
        mid = recommend_varieties(1500)
        self.assertIn("Khumal Seto-1", mid)
        self.assertEqual(len(mid), 6)
        high = recommend_varieties(2400)
        self.assertIn("Rojita", high)
        self.assertNotIn("MS 42.3", high)  # band tops out at 1600

    def test_challenges_conditions(self):
        out = challenges(
            annual_rain=2600, alt=855, coldest_c=15.5,
            clay_pct=32, ph=4.9, climate_zone_name="Tropical",
        )
        self.assertIn("Monsoon disease pressure (Jun–Sep)", out)
        self.assertIn("Waterlogging on heavy soils", out)
        self.assertIn("Heat stress in late crop (Mar–May)", out)
        self.assertIn("Soil too acidic — apply lime before planting", out)
        self.assertNotIn("Frost risk at planting or harvest", out)
        cold = challenges(
            annual_rain=400, alt=3400, coldest_c=-6,
            clay_pct=None, ph=None, climate_zone_name="Alpine",
        )
        self.assertIn("Frost risk at planting or harvest", cold)
        self.assertIn("Low rainfall — irrigation needed", cold)

    def test_tips_static_plus_conditional(self):
        base = tips(annual_rain=1200, clay_pct=15, ph=5.8, alt=855)
        self.assertTrue(any("raised beds" in x for x in base))
        self.assertTrue(any("Khumal" in x for x in base))
        heavy = tips(annual_rain=2600, clay_pct=35, ph=4.9, alt=855)
        self.assertTrue(any("drainage channels" in x for x in heavy))
        self.assertTrue(any("late blight" in x for x in heavy))
        self.assertTrue(any("lime" in x for x in heavy))

    def test_overall_score_and_bands(self):
        factors = {
            "altitude": {"score": 100}, "temp": {"score": 100},
            "soil": {"score": 95}, "rainfall": {"score": 80},
            "climate": {"score": 85},
        }
        score = overall_score(factors)
        self.assertEqual(score, round(0.25*100 + 0.25*100 + 0.25*95 + 0.15*80 + 0.10*85))
        self.assertEqual(band_of(86), "Excellent")
        self.assertEqual(band_of(70), "Good")
        self.assertEqual(band_of(55), "Fair")
        self.assertEqual(band_of(40), "Poor")


class BuildAnalysisTest(unittest.TestCase):
    def test_full_payload_matches_spec_schema(self):
        out = build_analysis(
            lat=28.2132, lon=83.9908, alt=855, temp_c=19.0,
            coldest_c=11.5, annual_rain=2800, soil=OK_SOIL,
            place="पोखरा, कास्की, गण्डकी प्रदेश, नेपाल",
        )
        for key in (
            "score", "band", "place", "coords", "altitude_m", "recommendation",
            "summary", "factors", "challenges", "rainfall_zone", "region", "varieties", "tips",
        ):
            self.assertIn(key, out)
        self.assertEqual(out["coords"], {"lat": 28.2132, "lon": 83.9908})
        self.assertEqual(out["altitude_m"], 855)
        self.assertEqual(len(out["factors"]), 5)
        keys = [f["key"] for f in out["factors"]]
        self.assertEqual(keys, ["altitude", "temp", "rainfall", "soil", "climate"])
        for f in out["factors"]:
            self.assertIn("label", f)
            self.assertIn("value", f)
            self.assertIn("why", f)
            self.assertIn("vars", f)
            self.assertTrue(0 <= f["score"] <= 100)
        self.assertEqual(out["summary"]["soil"], "Sandy Loam")
        self.assertEqual(out["summary"]["season"], "Winter (Oct–Feb) & Spring (Mar–May)")
        self.assertEqual(out["rainfall_zone"], "Sub-tropical")
        self.assertEqual(out["region"], "mid-hills")
        self.assertIn("{zone}", out["recommendation"])  # template, substituted client-side
        self.assertIsInstance(out["challenges"], list)
        self.assertIsInstance(out["varieties"], list)
        self.assertIsInstance(out["tips"], list)
        self.assertIn(out["band"], ("Excellent", "Good", "Fair", "Poor"))

    def test_missing_soil_and_place_degrade_to_estimates(self):
        out = build_analysis(
            lat=20.0, lon=80.0, alt=150, temp_c=24.0,
            coldest_c=15.0, annual_rain=None, soil=None, place=None,
        )
        soil_f = next(f for f in out["factors"] if f["key"] == "soil")
        self.assertEqual(soil_f["value"], "Alluvial Sandy Loam")
        self.assertEqual(out["place"], "Your location")
        rain_f = next(f for f in out["factors"] if f["key"] == "rainfall")
        self.assertEqual(rain_f["score"], 75)  # neutral when climate data missing


if __name__ == "__main__":
    unittest.main()
