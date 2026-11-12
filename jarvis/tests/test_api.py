import pytest
from fastapi.testclient import TestClient

H = {"X-Jarvis": "1"}


@pytest.fixture
def client(house):
    from jarvis.main import create_app

    return TestClient(create_app(house), client=("192.168.1.5", 50000))


def login(client):
    assert client.post("/api/setup", json={"password": "langes-passwort"}, headers=H).status_code == 200


def test_setup_once_then_login(client):
    assert client.get("/api/session").json()["setup_required"] is True
    login(client)
    assert client.post("/api/setup", json={"password": "anderes-passwort"}, headers=H).status_code == 409
    client.post("/api/logout", headers=H)
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/login", json={"password": "falsch-falsch"}, headers=H).status_code == 401
    assert client.post("/api/login", json={"password": "langes-passwort"}, headers=H).status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_short_password_rejected(client):
    assert client.post("/api/setup", json={"password": "kurz"}, headers=H).status_code == 400


def test_state_change_needs_custom_header(client):
    login(client)
    assert client.put("/api/settings", json={"values": {"voice.speed": 1.1}}).status_code == 403


def test_public_address_is_refused(house):
    from jarvis.main import create_app

    app = create_app(house)
    assert TestClient(app, client=("8.8.8.8", 1)).get("/api/session").status_code == 403
    proxied = TestClient(app, client=("172.18.0.4", 1))
    assert proxied.get("/api/session", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert proxied.get("/api/session", headers={"X-Forwarded-For": "192.168.1.9"}).status_code == 200


def test_secrets_are_write_only(client):
    login(client)
    assert client.put("/api/secrets", json={"key": "gemini.api_key", "value": "AIza-secret"}, headers=H).json()["set"]
    settings = client.get("/api/settings").json()
    assert settings["secrets"]["gemini.api_key"] is True
    assert "AIza-secret" not in client.get("/api/settings").text
    assert client.put("/api/secrets", json={"key": "anything", "value": "x"}, headers=H).status_code == 400


def test_settings_validation(client):
    login(client)
    ok = client.put("/api/settings", json={"values": {"gemini.model": "gemini-3.8-flash", "mic.sensitivity": 70}}, headers=H)
    assert ok.status_code == 200
    bad = client.put("/api/settings", json={"values": {"house.timezone": "Mars/Olympus"}}, headers=H)
    assert bad.status_code == 400 and "house.timezone" in bad.json()["fields"]


def test_areas_people_and_recommendation(client):
    login(client)
    area = client.post("/api/areas", json={"name": "Wohnbereich"}, headers=H).json()
    assert client.put(f"/api/areas/{area['id']}", json={"rooms": ["Wohnzimmer"]}, headers=H).json()["rooms"] == ["Wohnzimmer"]
    person = client.post("/api/people", json={"name": "Luca", "anrede": "sir", "notes": "mag Jazz"}, headers=H).json()
    assert person["has_voice"] is False
    assert client.post("/api/people", json={"name": "X", "anrede": "lord"}, headers=H).status_code == 400
    mics = client.get("/api/mics").json()
    assert mics["recommendation"]["board"] == "Seeed XIAO ESP32-S3 Sense"
    assert {r["article"] for r in mics["recommendation"]["reichelt"]} == {"406873", "358353"}


def test_pair_requires_house_address(client):
    login(client)
    assert client.post("/api/mics/pair", json={"ip": "8.8.8.8", "name": "jarvis-mic-abc123"}, headers=H).status_code == 400


def test_index_has_strict_csp(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "script-src 'self'" in res.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
