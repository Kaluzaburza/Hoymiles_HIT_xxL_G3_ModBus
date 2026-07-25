"""Validate that every Home Assistant entity referenced by a dashboard exists."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import yaml


ENTITY_ID = re.compile(
    r"^(?:automation|binary_sensor|button|input_boolean|input_datetime|"
    r"input_number|number|script|select|sensor|switch)\.[a-z0-9_]+$"
)


def collect_entity_ids(value: object, result: set[str]) -> None:
    if isinstance(value, dict):
        for child in value.values():
            collect_entity_ids(child, result)
    elif isinstance(value, list):
        for child in value:
            collect_entity_ids(child, result)
    elif isinstance(value, str) and ENTITY_ID.fullmatch(value):
        result.add(value)


def main() -> int:
    dashboard_path = Path(
        sys.argv[1] if len(sys.argv) > 1 else "/config/esphome/dashboard_hoymiles.yaml"
    )
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")

    with dashboard_path.open("r", encoding="utf-8") as dashboard_file:
        dashboard = yaml.safe_load(dashboard_file)

    referenced: set[str] = set()
    collect_entity_ids(dashboard, referenced)

    request = urllib.request.Request(
        "http://supervisor/core/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        states = json.load(response)

    available = {state["entity_id"] for state in states}
    missing = sorted(referenced - available)
    print(f"Referenced entities: {len(referenced)}")
    print(f"Available referenced entities: {len(referenced) - len(missing)}")
    if missing:
        print("Missing entities:")
        for entity_id in missing:
            print(f"- {entity_id}")
        return 1

    print("Missing entities: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
