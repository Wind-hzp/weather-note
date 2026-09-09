#!/usr/bin/env python3
"""Weather transition monitor designed for GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
STATE_VERSION = 1

WEATHER_NAMES = {
    "rain": "下雨",
    "snow": "下雪",
    "thunderstorm": "雷暴",
    "fog": "大雾",
    "wind": "大风",
    "hot": "高温",
    "cold": "低温",
    "cloudy": "阴天",
    "clear": "晴天",
}

WMO_DESCRIPTIONS = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴并伴有小冰雹",
    99: "雷暴并伴有大冰雹",
}

RAIN_CODES = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
THUNDERSTORM_CODES = {95, 96, 99}
FOG_CODES = {45, 48}
CLOUDY_CODES = {1, 2, 3}
CLEAR_CODES = {0}
SUPPORTED_TYPES = set(WEATHER_NAMES)


class WeatherPushError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurrentWeather:
    code: int
    temperature_c: float
    apparent_temperature_c: float
    precipitation_mm: float
    wind_mps: float
    observed_at: str


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    allow_non_json: bool = False,
) -> dict[str, Any]:
    request_headers = {"User-Agent": "github-weather-push/1.0"}
    if headers:
        request_headers.update(headers)
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if allow_non_json:
                    return {"raw": raw}
                raise
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WeatherPushError(f"HTTP {exc.code}: {detail[:300]}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WeatherPushError(f"网络请求失败: {exc}") from exc


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeatherPushError(f"无法读取配置 {path}: {exc}") from exc

    location = config.get("location", {})
    for field in ("name", "latitude", "longitude", "timezone"):
        if field not in location:
            raise WeatherPushError(f"配置缺少 location.{field}")

    enabled = config.get("enabled_weather", [])
    if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
        raise WeatherPushError("enabled_weather 必须是天气类型字符串数组")
    unknown = set(enabled) - SUPPORTED_TYPES
    if unknown:
        raise WeatherPushError(f"不支持的天气类型: {', '.join(sorted(unknown))}")
    if not enabled:
        raise WeatherPushError("enabled_weather 至少要选择一种天气")
    try:
        latitude = float(location["latitude"])
        longitude = float(location["longitude"])
    except (TypeError, ValueError) as exc:
        raise WeatherPushError("经纬度必须是数字") from exc
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise WeatherPushError("经纬度超出有效范围")
    notifications = config.get("notifications", ["github"])
    if not isinstance(notifications, list) or not all(isinstance(item, str) for item in notifications):
        raise WeatherPushError("notifications 必须是通知渠道字符串数组")
    if not notifications:
        raise WeatherPushError("notifications 至少要配置一个通知渠道")
    return config


def apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Allow GitHub Variables/Secrets to override public config values."""
    result = json.loads(json.dumps(config))
    location = result["location"]

    overrides: tuple[tuple[str, str, Any], ...] = (
        ("LOCATION_NAME", "name", str),
        ("LATITUDE", "latitude", float),
        ("LONGITUDE", "longitude", float),
        ("TIMEZONE", "timezone", str),
    )
    for env_name, field, converter in overrides:
        value = os.getenv(env_name)
        if value:
            try:
                location[field] = converter(value)
            except ValueError as exc:
                raise WeatherPushError(f"环境变量 {env_name} 格式不正确") from exc

    weather_types = os.getenv("WEATHER_TYPES", "").strip()
    if weather_types:
        enabled = [item.strip().lower() for item in weather_types.split(",") if item.strip()]
        unknown = set(enabled) - SUPPORTED_TYPES
        if unknown:
            raise WeatherPushError(f"WEATHER_TYPES 含不支持的类型: {', '.join(sorted(unknown))}")
        result["enabled_weather"] = enabled

    channels = os.getenv("NOTIFICATION_CHANNELS", "").strip()
    if channels:
        result["notifications"] = [item.strip().lower() for item in channels.split(",") if item.strip()]
    return result


def fetch_current_weather(config: dict[str, Any]) -> CurrentWeather:
    location = config["location"]
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": location["timezone"],
        "current": (
            "temperature_2m,apparent_temperature,precipitation,"
            "weather_code,wind_speed_10m"
        ),
        "wind_speed_unit": "ms",
    }
    query = "&".join(f"{key}={quote(str(value))}" for key, value in params.items())
    data = http_json(f"{OPEN_METEO_URL}?{query}")
    try:
        current = data["current"]
        return CurrentWeather(
            code=int(current["weather_code"]),
            temperature_c=float(current["temperature_2m"]),
            apparent_temperature_c=float(current["apparent_temperature"]),
            precipitation_mm=float(current["precipitation"]),
            wind_mps=float(current["wind_speed_10m"]),
            observed_at=str(current["time"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherPushError(f"天气接口返回格式异常: {data}") from exc


def classify_weather(weather: CurrentWeather, thresholds: dict[str, Any]) -> set[str]:
    active: set[str] = set()
    if weather.code in RAIN_CODES or (
        weather.precipitation_mm > 0 and weather.code not in SNOW_CODES
    ):
        active.add("rain")
    if weather.code in SNOW_CODES:
        active.add("snow")
    if weather.code in THUNDERSTORM_CODES:
        active.add("thunderstorm")
    if weather.code in FOG_CODES:
        active.add("fog")
    if weather.code in CLOUDY_CODES:
        active.add("cloudy")
    if weather.code in CLEAR_CODES:
        active.add("clear")
    if weather.wind_mps >= float(thresholds.get("wind_mps", 10.8)):
        active.add("wind")
    if weather.temperature_c >= float(thresholds.get("hot_c", 35)):
        active.add("hot")
    if weather.temperature_c <= float(thresholds.get("cold_c", 0)):
        active.add("cold")
    return active


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "active": [], "updated_at": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != STATE_VERSION:
            return {"version": STATE_VERSION, "active": [], "updated_at": None}
        return state
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "active": [], "updated_at": None}


def save_state(path: Path, active: set[str], weather: CurrentWeather) -> None:
    state = {
        "version": STATE_VERSION,
        "active": sorted(active),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "last_observation": {
            "weather_code": weather.code,
            "observed_at": weather.observed_at,
        },
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notification_text(config: dict[str, Any], started: set[str], weather: CurrentWeather) -> tuple[str, str]:
    location_name = config["location"]["name"]
    type_names = "、".join(WEATHER_NAMES[item] for item in sorted(started))
    description = WMO_DESCRIPTIONS.get(weather.code, f"天气代码 {weather.code}")
    title = f"{location_name}天气提醒：{type_names}"
    body = (
        f"{location_name}现在出现{type_names}。当前{description}，"
        f"气温 {weather.temperature_c:g}°C，体感 {weather.apparent_temperature_c:g}°C，"
        f"降水 {weather.precipitation_mm:g} mm，风速 {weather.wind_mps:g} m/s。"
        f"观测时间：{weather.observed_at}。"
    )
    return title, body


def send_github(title: str, body: str) -> None:
    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    if not token or not repository:
        raise WeatherPushError("GitHub 通知需要 GITHUB_TOKEN 和 GITHUB_REPOSITORY")
    http_json(
        f"https://api.github.com/repos/{repository}/issues",
        method="POST",
        payload={"title": f"🌦️ [weather-alert] {title}", "body": body},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def send_pushplus(title: str, body: str) -> None:
    token = os.getenv("PUSHPLUS_TOKEN")
    if not token:
        raise WeatherPushError("PushPlus 通知需要 PUSHPLUS_TOKEN")
    response = http_json(
        "https://www.pushplus.plus/send",
        method="POST",
        payload={"token": token, "title": title, "content": body, "template": "txt"},
    )
    if response.get("code") != 200:
        raise WeatherPushError(f"PushPlus 返回失败: {response}")


def send_serverchan(title: str, body: str) -> None:
    sendkey = os.getenv("SERVERCHAN_SENDKEY")
    if not sendkey:
        raise WeatherPushError("Server酱通知需要 SERVERCHAN_SENDKEY")
    response = http_json(
        f"https://sctapi.ftqq.com/{quote(sendkey)}.send",
        method="POST",
        payload={"title": title, "desp": body},
    )
    if response.get("code") not in (0, None):
        raise WeatherPushError(f"Server酱返回失败: {response}")


def send_bark(title: str, body: str) -> None:
    bark_url = os.getenv("BARK_URL", "https://api.day.app").rstrip("/")
    bark_key = os.getenv("BARK_KEY")
    if not bark_key:
        raise WeatherPushError("Bark 通知需要 BARK_KEY")
    response = http_json(
        f"{bark_url}/push",
        method="POST",
        payload={"device_key": bark_key, "title": title, "body": body, "group": "天气提醒"},
    )
    if response.get("code") not in (200, None):
        raise WeatherPushError(f"Bark 返回失败: {response}")


def send_telegram(title: str, body: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise WeatherPushError("Telegram 通知需要 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
    response = http_json(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        method="POST",
        payload={"chat_id": chat_id, "text": f"{title}\n\n{body}"},
    )
    if response.get("ok") is not True:
        raise WeatherPushError(f"Telegram 返回失败: {response}")


def send_webhook(title: str, body: str, started: set[str], weather: CurrentWeather) -> None:
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise WeatherPushError("Webhook 通知需要 WEBHOOK_URL")
    http_json(
        webhook_url,
        method="POST",
        payload={
            "title": title,
            "body": body,
            "weather_types": sorted(started),
            "weather_code": weather.code,
            "observed_at": weather.observed_at,
        },
        allow_non_json=True,
    )


def send_notifications(
    channels: list[str],
    title: str,
    body: str,
    started: set[str],
    weather: CurrentWeather,
) -> bool:
    senders = {
        "github": lambda: send_github(title, body),
        "pushplus": lambda: send_pushplus(title, body),
        "serverchan": lambda: send_serverchan(title, body),
        "bark": lambda: send_bark(title, body),
        "telegram": lambda: send_telegram(title, body),
        "webhook": lambda: send_webhook(title, body, started, weather),
    }
    succeeded = 0
    for channel in channels:
        sender = senders.get(channel)
        if not sender:
            print(f"::warning::忽略未知通知渠道: {channel}", file=sys.stderr)
            continue
        try:
            sender()
            succeeded += 1
            print(f"通知已通过 {channel} 发送")
        except WeatherPushError as exc:
            print(f"::error::{channel} 通知失败: {exc}", file=sys.stderr)
    return succeeded > 0


def run(config_path: Path, state_path: Path, dry_run: bool = False) -> int:
    config = apply_env_overrides(load_config(config_path))
    weather = fetch_current_weather(config)
    enabled = set(config["enabled_weather"])
    active = classify_weather(weather, config.get("thresholds", {})) & enabled
    previous = set(load_state(state_path).get("active", []))
    started = active - previous

    print(
        f"{config['location']['name']}: {WMO_DESCRIPTIONS.get(weather.code, weather.code)}, "
        f"当前命中={sorted(active)}, 上次命中={sorted(previous)}"
    )

    should_save = active != previous
    if started:
        title, body = notification_text(config, started, weather)
        if dry_run:
            print(f"[DRY RUN] {title}\n{body}")
        else:
            channels = config.get("notifications", ["github"])
            if not send_notifications(channels, title, body, started, weather):
                print("::error::所有通知渠道均发送失败；保留旧状态以便下次重试", file=sys.stderr)
                return 1
    # A manual dry run must not consume the transition; otherwise a real run
    # immediately afterwards would incorrectly think the alert was sent.
    if should_save and not dry_run:
        save_state(state_path, active, weather)
        print(f"状态已更新: {sorted(active)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="天气变化推送")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path(".weather-state.json"))
    parser.add_argument("--dry-run", action="store_true", help="只打印通知，不实际发送")
    args = parser.parse_args()
    try:
        return run(args.config, args.state, args.dry_run)
    except WeatherPushError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
