"""Deploy a storage-mode Home Assistant dashboard through its WebSocket API."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import websocket
import yaml


def receive_json(connection: websocket.WebSocket) -> dict:
    message = json.loads(connection.recv())
    if not isinstance(message, dict):
        raise RuntimeError(f"Unexpected WebSocket response: {message!r}")
    return message


def request(connection: websocket.WebSocket, message_id: int, message: dict) -> dict:
    connection.send(json.dumps({"id": message_id, **message}))
    response = receive_json(connection)
    if response.get("id") != message_id:
        raise RuntimeError(f"Unexpected response id: {response!r}")
    if not response.get("success"):
        raise RuntimeError(f"Home Assistant rejected the request: {response!r}")
    return response


def main() -> int:
    dashboard_path = Path(
        sys.argv[1] if len(sys.argv) > 1 else "/config/esphome/dashboard_hoymiles.yaml"
    )
    url_path = sys.argv[2] if len(sys.argv) > 2 else "hit-10l-g3"
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")

    with dashboard_path.open("r", encoding="utf-8") as dashboard_file:
        config = yaml.safe_load(dashboard_file)

    if not isinstance(config, dict) or not isinstance(config.get("views"), list):
        raise ValueError("Dashboard YAML must contain a top-level 'views' list")

    connection = websocket.create_connection(
        "ws://supervisor/core/websocket",
        timeout=20,
        suppress_origin=True,
    )
    try:
        auth_required = receive_json(connection)
        if auth_required.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected authentication response: {auth_required!r}")

        connection.send(json.dumps({"type": "auth", "access_token": token}))
        auth_response = receive_json(connection)
        if auth_response.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant authentication failed: {auth_response!r}")

        current = request(
            connection,
            1,
            {"type": "lovelace/config", "url_path": url_path},
        )["result"]

        backup_dir = dashboard_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"dashboard_{url_path}_{timestamp}.json"
        backup_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        request(
            connection,
            2,
            {
                "type": "lovelace/config/save",
                "url_path": url_path,
                "config": config,
            },
        )
        deployed = request(
            connection,
            3,
            {"type": "lovelace/config", "url_path": url_path},
        )["result"]
    finally:
        connection.close()

    if deployed != config:
        raise RuntimeError("Dashboard read-back differs from the submitted configuration")

    paths = [view.get("path") for view in deployed["views"]]
    print(f"Dashboard '{url_path}' deployed: {len(paths)} views")
    print(f"View paths: {', '.join(str(path) for path in paths)}")
    print(f"Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
