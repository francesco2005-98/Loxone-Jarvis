import asyncio

from jarvis.audio.audioserver import AudioserverClient, AudioserverError
from jarvis.audio.tts import Piper
from jarvis.db import Database
from jarvis.settings import Settings


class _Events:
    def log(self, *a, **k):
        pass


def _piper(tmp_path):
    piper = Piper("http://piper", Settings(Database(":memory:")), cache_dir=tmp_path / "tts")
    calls = []

    async def post(payload):
        calls.append(payload["text"])
        await asyncio.sleep(0.05)
        return b"RIFF" + payload["text"].encode()

    piper._post = post
    return piper, calls


def test_a_sentence_started_early_is_synthesized_once(tmp_path):
    piper, calls = _piper(tmp_path)

    async def go():
        piper.prerender("Sehr wohl, Sir.", "de")
        await asyncio.sleep(0.01)
        return await piper.synthesize("Sehr wohl, Sir.", "de")

    assert asyncio.run(go()).startswith(b"RIFF")
    assert len(calls) == 1, "say() waits for the running synthesis instead of starting another"


def test_spoken_sentences_survive_a_restart(tmp_path):
    first, calls = _piper(tmp_path)

    async def go(piper):
        audio = await piper.synthesize("Das Licht ist an.", "de")
        await asyncio.sleep(0.05)
        return audio

    audio = asyncio.run(go(first))
    second, again = _piper(tmp_path)
    assert asyncio.run(go(second)) == audio
    assert calls and not again, "the new process reads the recording from disk"


class _Client(AudioserverClient):
    def __init__(self):
        super().__init__("192.168.1.40", miniserver=None, events=_Events())
        self._ready.set()
        self.uploads = []
        self.plays = []
        self.missing = False

    async def _upload(self, filename, wav):
        self.uploads.append(filename)
        self._uploaded.add(filename)

    async def _command(self, cmd, result_key, timeout=8.0):
        self.plays.append(cmd)
        if self.missing:
            self.missing = False
            raise AudioserverError("file not found")
        return True


def test_the_same_words_play_without_another_upload():
    client = _Client()

    async def go():
        first = await client.announce([(3, 40)], b"RIFF-sehr-wohl")
        client._announcements.clear()
        second = await client.announce([(3, 40)], b"RIFF-sehr-wohl")
        return first, second

    first, second = asyncio.run(go())
    assert first.filename == second.filename
    assert len(client.uploads) == 1 and len(client.plays) == 2


def test_a_file_the_server_lost_is_uploaded_again():
    client = _Client()

    async def go():
        await client.announce([(3, 40)], b"RIFF-x")
        client._announcements.clear()
        client.missing = True
        await client.announce([(3, 40)], b"RIFF-x")

    asyncio.run(go())
    assert len(client.uploads) == 2


def test_the_same_words_playing_twice_at_once_get_their_own_file():
    client = _Client()

    async def go():
        a = await client.announce([(3, 40)], b"RIFF-y")
        b = await client.announce([(4, 40)], b"RIFF-y")
        return a, b

    a, b = asyncio.run(go())
    assert a.filename != b.filename
