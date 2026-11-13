"""Where the house stands: address search, reverse lookup and the sun times for that place.

Address search uses OpenStreetMap Nominatim, only when someone searches in
the interface. The sun times are computed locally.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone, tzinfo

import aiohttp

NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = "Loxone-Jarvis/1.0 (home voice assistant)"


class LocationError(RuntimeError):
    pass


async def _get(path: str, params: dict) -> object:
    timeout = aiohttp.ClientTimeout(total=8)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "de,en"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
            async with http.get(f"{NOMINATIM}/{path}", params={"format": "jsonv2", **params}) as resp:
                if resp.status != 200:
                    raise LocationError(f"Kartendienst antwortet mit {resp.status}")
                return await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise LocationError("Kartendienst nicht erreichbar") from exc


async def search(query: str, limit: int = 5) -> list[dict]:
    data = await _get("search", {"q": query.strip()[:200], "limit": max(1, min(10, limit))})
    return [
        {"label": str(item.get("display_name", "")), "lat": round(float(item["lat"]), 5), "lon": round(float(item["lon"]), 5)}
        for item in data or []
        if isinstance(item, dict) and "lat" in item and "lon" in item
    ]


async def reverse(lat: float, lon: float) -> str:
    data = await _get("reverse", {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "zoom": 18})
    label = data.get("display_name") if isinstance(data, dict) else None
    return str(label or f"{lat:.5f}, {lon:.5f}")


def sun_times(lat: float, lon: float, day: date, tz: tzinfo) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset (NOAA approximation, about a minute accurate). None in polar day or night."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    phi = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    if not -1.0 <= cos_ha <= 1.0:
        return None, None
    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rise = midnight + timedelta(minutes=720 - 4 * (lon + ha) - eqtime)
    sets = midnight + timedelta(minutes=720 - 4 * (lon - ha) - eqtime)
    return rise.astimezone(tz), sets.astimezone(tz)
