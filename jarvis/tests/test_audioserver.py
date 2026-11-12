import asyncio
import base64
from urllib.parse import unquote

from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from jarvis.audio import wav
from jarvis.audio.audioserver import Announcement, AudioserverClient, build_authenticate


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
    def __init__(self):
        self.structure = type("S", (), {"audioserver_host": lambda self: None})()

    def current_token(self):
        return None


class _Events:
    def log(self, *a, **k):
        pass


def test_announcement_lifecycle_from_events():
    client = AudioserverClient(settings=None, miniserver=_Stub(), events=_Events())
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
