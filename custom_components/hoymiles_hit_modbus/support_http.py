"""Authenticated HTTP download for Hoymiles support diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from aiohttp import web

from homeassistant.components.http.view import HomeAssistantView
from homeassistant.const import __version__ as HOME_ASSISTANT_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from .const import DOMAIN
from .diagnostic_bundle import build_support_archive
from .diagnostics import async_get_config_entry_diagnostics
from .installation_identity import async_get_or_create_installation_identity


SUPPORT_BUNDLE_URL = f"/api/{DOMAIN}/support-bundle"


class HoymilesSupportBundleView(HomeAssistantView):
    """Generate a browser-downloadable diagnostic ZIP for administrators."""

    url = SUPPORT_BUNDLE_URL
    name = f"api:{DOMAIN}:support_bundle"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return a fresh ZIP without persisting it in /config."""
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            raise Unauthorized()

        hass: HomeAssistant = request.app["hass"]
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise web.HTTPNotFound(text="Hoymiles integration is not configured")

        installation_identity = (
            await async_get_or_create_installation_identity(hass)
        )
        reports = [
            await async_get_config_entry_diagnostics(hass, entry)
            for entry in entries
        ]
        now = datetime.now(timezone.utc)
        build_archive = partial(
            build_support_archive,
            reports,
            log_path=Path(hass.config.path("home-assistant.log")),
            generated_at=now.isoformat(),
            home_assistant_version=HOME_ASSISTANT_VERSION,
            **installation_identity.as_dict(),
        )
        archive = await hass.async_add_executor_job(build_archive)
        filename = f"hoymiles_diagnostics_{now:%Y%m%dT%H%M%SZ}.zip"
        return web.Response(
            body=archive,
            content_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
