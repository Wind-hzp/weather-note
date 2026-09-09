import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.configure_weather import selected_from_environment
from src.weather_push import (
    CurrentWeather,
    apply_env_overrides,
    classify_weather,
    load_state,
    run,
    run_notification_test,
    save_state,
)


def sample_weather(**overrides):
    values = {
        "code": 0,
        "temperature_c": 20.0,
        "apparent_temperature_c": 20.0,
        "precipitation_mm": 0.0,
        "wind_mps": 2.0,
        "observed_at": "2026-09-09T12:00",
    }
    values.update(overrides)
    return CurrentWeather(**values)


class WeatherClassificationTests(unittest.TestCase):
    def test_rain_code_is_rain(self):
        active = classify_weather(sample_weather(code=63, precipitation_mm=1.2), {})
        self.assertIn("rain", active)

    def test_thunderstorm_is_also_rain(self):
        active = classify_weather(sample_weather(code=95), {})
        self.assertEqual({"rain", "thunderstorm"}, active)

    def test_snow_precipitation_is_not_mislabeled_as_rain(self):
        active = classify_weather(sample_weather(code=73, precipitation_mm=1.0), {})
        self.assertEqual({"snow"}, active)

    def test_threshold_weather(self):
        active = classify_weather(
            sample_weather(temperature_c=36, wind_mps=12),
            {"hot_c": 35, "cold_c": 0, "wind_mps": 10.8},
        )
        self.assertTrue({"clear", "hot", "wind"}.issubset(active))


class ConfigTests(unittest.TestCase):
    def test_environment_overrides(self):
        config = {
            "location": {"name": "A", "latitude": 1, "longitude": 2, "timezone": "UTC"},
            "enabled_weather": ["rain"],
            "notifications": ["github"],
        }
        env = {
            "LOCATION_NAME": "杭州",
            "LATITUDE": "30.27",
            "LONGITUDE": "120.15",
            "WEATHER_TYPES": "rain,snow,fog",
            "NOTIFICATION_CHANNELS": "pushplus,bark",
        }
        with patch.dict(os.environ, env, clear=True):
            result = apply_env_overrides(config)
        self.assertEqual("杭州", result["location"]["name"])
        self.assertEqual(30.27, result["location"]["latitude"])
        self.assertEqual(["rain", "snow", "fog"], result["enabled_weather"])
        self.assertEqual(["pushplus", "bark"], result["notifications"])

    def test_checkbox_selection(self):
        with patch.dict(
            os.environ,
            {"SELECT_RAIN": "true", "SELECT_FOG": "true", "SELECT_HOT": "false"},
            clear=True,
        ):
            self.assertEqual(["rain", "fog"], selected_from_environment())

    def test_checkbox_selection_requires_one_type(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "至少需要"):
                selected_from_environment()


class StateTests(unittest.TestCase):
    def test_missing_state_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            state = load_state(Path(folder) / "missing.json")
        self.assertEqual([], state["active"])

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            save_state(path, {"rain"}, sample_weather(code=61))
            state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(["rain"], state["active"])
        self.assertEqual(61, state["last_observation"]["weather_code"])

    def test_dry_run_does_not_consume_transition(self):
        config = {
            "location": {"name": "测试", "latitude": 1, "longitude": 2, "timezone": "UTC"},
            "enabled_weather": ["rain"],
            "notifications": ["github"],
        }
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "config.json"
            state_path = Path(folder) / "state.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            with patch("src.weather_push.fetch_current_weather", return_value=sample_weather(code=61)):
                exit_code = run(config_path, state_path, dry_run=True)
            self.assertEqual(0, exit_code)
            self.assertFalse(state_path.exists())


class NotificationTests(unittest.TestCase):
    def test_notification_test_uses_configured_channel(self):
        config = {
            "location": {"name": "测试城市", "latitude": 1, "longitude": 2, "timezone": "UTC"},
            "enabled_weather": ["rain"],
            "notifications": ["pushplus"],
        }
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            with patch("src.weather_push.send_notifications", return_value=True) as sender:
                exit_code = run_notification_test(config_path)
        self.assertEqual(0, exit_code)
        self.assertEqual(["pushplus"], sender.call_args.args[0])
        self.assertEqual("天气推送测试成功", sender.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
