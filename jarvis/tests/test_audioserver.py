import asyncio
import base64
from urllib.parse import unquote

from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from jarvis.audio import wav
from jarvis.db import Database
from jarvis.loxone.structure import parse
from jarvis.settings import Settings
from jarvis.audio.audioserver import (
    Announcement,
    AudioserverClient,
    Audioservers,
    build_authenticate,
    parse_hosts,
    search_command,
    search_items,
    service_accounts,
    serviceplay_command,
)


def test_authenticate_command_decrypts_to_jwt():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    one_line = pem.replace("\n", "")
    cmd = build_authenticate("jarvis", "eyJ.token.sig", one_line, "SESSION42")
    _, _, user, rsa_b64, aes_b64 = cmd.split("/")
    assert user == "jarvis"
    plain = private.decrypt(base64.b64decode(unquote(rsa_b64)), padding.PKCS1v15()).decode()
    key_hex, iv_hex, session = plain.split(":")
    assert session == "SESSION42"
    dec = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.CBC(bytes.fromhex(iv_hex))).decryptor()
    data = dec.update(base64.b64decode(unquote(aes_b64))) + dec.finalize()
    unpad = sympad.PKCS7(128).unpadder()
    assert unpad.update(data) + unpad.finalize() == b"eyJ.token.sig"


class _Stub:
    connected = True

    def __init__(self, structure=None):
        self.structure = structure or parse({})

    def current_token(self):
        return None


class _Events:
    def log(self, *a, **k):
        pass


def test_announcement_lifecycle_from_events():
    client = AudioserverClient("192.168.1.40", miniserver=_Stub(), events=_Events())
    seen = []
    client.on_zone_event(lambda pid, active: seen.append((pid, active)))
    ann = Announcement("jarvis-abc.wav", {3})
    client._announcements[ann.filename] = ann
    client._on_events([{"playerid": 5, "mode": "stop", "eventype": 7, "audiopath": "file:///x/jarvis-abc.wav"}])
    assert not ann.began.is_set()
    client._on_events([{"playerid": 3, "mode": "play", "eventype": 7, "audiopath": "file:///home/public/Event_Sounds/audioupload/jarvis-abc.wav"}])
    assert ann.began.is_set() and client.event_active(3)
    client._on_events([{"playerid": 3, "mode": "play", "audiopath": "spotify:track:1"}])
    assert ann.finished.is_set()
    assert seen == [(3, True), (3, False)]


def test_wav_pipeline_resamples_and_pads():
    import numpy as np

    pcm = wav.Pcm((np.sin(np.arange(22050) / 10) * 1000).astype("<i2"), 22050)
    out = wav.with_silence(wav.resample(pcm, 24000), 600)
    assert out.rate == 24000
    assert abs(out.duration - (1.0 + 0.6 + 0.25)) < 0.01
    data = wav.encode(out)
    assert data[:4] == b"RIFF"
    back = wav.decode(data)
    assert back.rate == 24000 and back.samples.size == out.samples.size


def test_spotify_search_and_play_commands():
    command, key = search_command("spotify", "luca@home", "Jazz", kind="playlist", limit=8)
    assert key == "search_result"
    assert command == "audio/cfg/search/spotify/luca%40home/playlist/Jazz/8/0"
    play, play_key = serviceplay_command(3, "spotify", "luca@home", "spotify:playlist:abc")
    assert play_key == "serviceplay_result"
    assert play == "audio/3/serviceplay/spotify/luca%40home/spotify%3Aplaylist%3Aabc"


def test_spotify_accounts_and_playlists_are_parsed():
    accounts = service_accounts(
        {"getservices_result": [{"service": "spotify", "user": "luca", "name": "Spotify"}, {"service": "tunein", "user": "x"}]}
    )
    assert accounts == [{"service": "spotify", "user": "luca", "name": "Spotify"}]
    items = search_items(
        {
            "tracks": {"items": [{"title": "So What", "audiopath": "spotify:track:1", "type": "track"}]},
            "playlists": {"items": [{"name": "Jazz Classics", "audiopath": "spotify:playlist:abc", "type": 3, "user": "luca"}]},
        }
    )
    assert items[0]["name"] == "Jazz Classics"
    assert items[0]["path"] == "spotify:playlist:abc"


TWO_SERVERS = {
    "mediaServer": {
        "as-eg": {"type": 2, "name": "Audioserver EG", "host": "192.168.1.40:7091"},
        "as-og": {"type": 2, "name": "Audioserver OG", "host": "192.168.1.41:7091"},
    },
    "controls": {
        "z-wohn": {"name": "Wohnzimmer", "type": "AudioZoneV2", "uuidAction": "z-wohn", "details": {"playerid": 1, "server": "as-eg"}},
        "z-kind": {"name": "Kinderzimmer", "type": "AudioZoneV2", "uuidAction": "z-kind", "details": {"playerid": 7, "server": "as-og"}},
    },
}


def _pool(structure=TWO_SERVERS, manual=""):
    settings = Settings(Database(":memory:"))
    if manual:
        settings.update({"audioserver.host": manual})
    pool = Audioservers(settings, _Stub(parse(structure)), _Events())
    pool.sync()
    return pool


def test_every_audioserver_is_imported_and_zones_route_to_their_own():
    pool = _pool()
    assert pool.hosts() == ["192.168.1.40", "192.168.1.41"]
    assert [s["name"] for s in pool.servers()] == ["Audioserver EG", "Audioserver OG"]
    assert pool.client_for(1).address == "192.168.1.40"
    assert pool.client_for(7).address == "192.168.1.41"
    assert pool.client_for(99) is None
    assert pool.status == "wartet auf Miniserver"
    pool.clients["192.168.1.40"]._ready.set()
    assert pool.status == "teilweise verbunden (1/2)"
    assert pool.ready_for(1) and not pool.ready_for(7)


def test_manual_addresses_replace_the_structure():
    pool = _pool(manual="10.0.0.5, 10.0.0.6:7091")
    assert pool.hosts() == ["10.0.0.5", "10.0.0.6"]
    assert pool.client_for(1) is None, "no structure host matches, nothing learned yet"
    pool._zone_event("10.0.0.6", 1, True)
    assert pool.client_for(1).address == "10.0.0.6"
    single = _pool(manual="10.0.0.5")
    assert single.client_for(7).address == "10.0.0.5"


def test_parse_hosts_and_setting_accept_lists():
    assert parse_hosts("192.168.1.40; 192.168.1.41:7091  192.168.1.40") == ["192.168.1.40", "192.168.1.41"]
    settings = Settings(Database(":memory:"))
    settings.update({"audioserver.host": "192.168.1.40, 192.168.1.41"})
    assert settings.get("audioserver.host") == "192.168.1.40, 192.168.1.41"


def test_announcement_is_split_per_audioserver():
    pool = _pool()
    sent = {}

    def fake(host):
        async def announce(targets, wav_bytes):
            sent[host] = targets
            ann = Announcement(f"{host}.wav", {p for p, _ in targets})
            ann.began.set()
            return ann

        return announce

    for host, client in pool.clients.items():
        client.announce = fake(host)

    async def scenario():
        combined = await pool.announce([(1, 40), (7, 30)], b"RIFF")
        await asyncio.wait_for(combined.began.wait(), 1)
        assert not combined.finished.is_set()
        return combined

    combined = asyncio.new_event_loop().run_until_complete(scenario())
    assert sent == {"192.168.1.40": [(1, 40)], "192.168.1.41": [(7, 30)]}
    assert combined.players == {1, 7}
