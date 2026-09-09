#!/usr/bin/env python3
"""Update enabled weather types from GitHub workflow checkbox inputs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from .weather_push import SUPPORTED_TYPES
except ImportError:  # Direct execution: python src/configure_weather.py
    from weather_push import SUPPORTED_TYPES


ORDERED_TYPES = (
    "rain",
    "snow",
    "thunderstorm",
    "fog",
    "wind",
    "hot",
    "cold",
    "cloudy",
    "clear",
)


def selected_from_environment() -> list[str]:
    selected = [
        weather_type
        for weather_type in ORDERED_TYPES
        if os.getenv(f"SELECT_{weather_type.upper()}", "false").lower() == "true"
    ]
    if not selected:
        raise ValueError("至少需要勾选一种天气类型")
    if not set(selected).issubset(SUPPORTED_TYPES):
        raise ValueError("选择中存在不支持的天气类型")
    return selected


def update_config(path: Path, selected: list[str]) -> None:
    config = json.loads(path.read_text(encoding="utf-8"))
    config["enabled_weather"] = selected
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    try:
        selected = selected_from_environment()
        update_config(Path("config.json"), selected)
        print("已选择天气类型：" + ", ".join(selected))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
