from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from jarvis import location
from jarvis.brain.tools import Turn
from jarvis.loxone.structure import parse
from jarvis.settings import SettingsError

H = {"X-Jarvis": "1"}


def test_sun_times_for_berlin_and_polar_night():
    berlin = ZoneInfo("Europe/Berlin")
    rise, sets = location.sun_times(52.52, 13.405, date(2026, 6, 21), berlin)
    assert abs((rise - datetime(2026, 6, 21, 4, 43, tzinfo=berlin)).total_seconds()) < 240
    assert abs((sets - datetime(2026, 6, 21, 21, 33, tzinfo=berlin)).total_seconds()) < 240
    assert location.sun_times(78.2, 15.6, date(2026, 12, 21), ZoneInfo("Europe/Oslo")) == (None, None)


def test_coordinates_are_validated(house):
    house.settings.update({"house.lat": "48.137154", "house.lon": 11.576124, "house.location": "Marienplatz, München"})
    assert house.settings.get("house.lat") == 48.13715
    with pytest.raises(SettingsError):
        house.settings.update({"house.lat": 95})
    house.settings.update({"house.lat": None, "house.lon": ""})
    assert house.settings.get("house.lat") is None and house.settings.get("house.lon") is None


def test_location_reaches_gemini(house):
    area = house.areas.create("Wohnbereich")
    turn = Turn(area=area, lang="de", text="Wie wird das Wetter?")
    assert "STANDORT" not in house.brain.system_for(turn, False)
    house.settings.update({"house.location": "Marienplatz 1, München", "house.lat": 48.13715, "house.lon": 11.57612})
    system = house.brain.system_for(turn, False)
    assert "STANDORT DES HAUSES: Marienplatz 1, München (Breite 48.137" in system
    assert "Sonnenaufgang" in system and "Sonnenuntergang" in system


def test_loxone_project_location_is_offered():
    assert parse({"msInfo": {"msName": "Haus", "location": "Kollerschlag"}}).location == "Kollerschlag"


@pytest.fixture
def client(house):
    from jarvis.main import create_app

    c = TestClient(create_app(house), client=("192.168.1.5", 50000))
    c.post("/api/setup", json={"password": "langes-passwort"}, headers=H)
    return c


def test_location_endpoints(client, house, monkeypatch):
    async def search(query, limit=5):
        assert query == "Marienplatz München"
        return [{"label": "Marienplatz, München", "lat": 48.13715, "lon": 11.57612}]

    async def reverse(lat, lon):
        raise location.LocationError("offline")

    monkeypatch.setattr(location, "search", search)
    monkeypatch.setattr(location, "reverse", reverse)
    res = client.post("/api/location/search", json={"query": "Marienplatz München"}, headers=H)
    assert res.json()["results"][0]["lat"] == 48.13715
    rev = client.post("/api/location/reverse", json={"lat": 48.137154, "lon": 11.576124}, headers=H).json()
    assert rev == {"label": "48.13715, 11.57612", "lat": 48.13715, "lon": 11.57612}
    house.settings.update({"house.location": "Marienplatz, München", "house.lat": 48.13715, "house.lon": 11.57612})
    loc = client.get("/api/overview").json()["location"]
    assert loc["label"] == "Marienplatz, München" and loc["lon"] == 11.57612
    assert "geolocation=(self)" in client.get("/").headers["permissions-policy"]
