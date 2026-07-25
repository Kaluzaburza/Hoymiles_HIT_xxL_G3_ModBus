"""Install optional dashboard and EMS assets into Home Assistant config."""

from __future__ import annotations

import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant


RESOURCE_ROOT = Path(__file__).with_name("resources")


def _copy_assets(config_path: Path, language: str, overwrite: bool) -> list[Path]:
    """Copy bundled assets and return paths that were written."""
    localized = "pl" if language.startswith("pl") else "en"
    sources = {
        RESOURCE_ROOT / f"dashboard_hoymiles_{localized}.yaml": (
            config_path / "dashboard_hoymiles.yaml"
        ),
        RESOURCE_ROOT
        / "home_assistant"
        / localized
        / "hoymiles_ems_scheduler.yaml": (
            config_path / "packages" / "hoymiles_ems_scheduler.yaml"
        ),
        RESOURCE_ROOT / "www" / "hoymiles-rce-chart-card.js": (
            config_path / "www" / "hoymiles-rce-chart-card.js"
        ),
    }
    written: list[Path] = []
    for source, destination in sources.items():
        if destination.exists() and not overwrite:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        written.append(destination)
    return written


async def async_install_assets(
    hass: HomeAssistant,
    *,
    overwrite: bool,
) -> list[Path]:
    """Install the optional assets without blocking Home Assistant."""
    config_path = Path(hass.config.config_dir)
    return await hass.async_add_executor_job(
        _copy_assets,
        config_path,
        hass.config.language,
        overwrite,
    )
