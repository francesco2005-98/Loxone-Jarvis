import asyncio

import numpy as np

from jarvis.audio import wav
from jarvis.audio.audioserver import Announcement, Audioservers
from jarvis.voice.arbitration import Phase
from jarvis.voice.coordinator import _hush
from jarvis.voice.mics import Mic
from jarvis.voice.stt import Transcript
from jarvis.voice.vad import Endpointer, VadConfig


def tone(seconds, amplitude):
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2").tobytes()


class FakeBoard:
    def __init__(self):
        self.switch = []
        self.number = []

    def switch_command(self, key, state):
        self.switch.append(state)

    def number_command(self, key, value):
        self.number.append(value)


def board(house, area_id, mic_id="m1"):
    mic = Mic(mic_id, f"jarvis-mic-{mic_id}", "Mikrofon", "aa:bb:cc:dd:ee:ff", None, area_id, None, None)
    mic.client = FakeBoard()
    mic.connected = True
    mic.switch_key = 1
    mic.number_key = 2
    house.mics.mics[mic.id] = mic
    return mic


def record_events(house):
    events = []
    house.mics.send = lambda mic, event, data=None: events.append(event)
    return events


def test_the_board_run_ends_in_one_message(house):
    """A RUN_END that follows STT_VAD_END too fast is lost on the board, which then never wakes again."""
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    events = record_events(house)
    coordinator = house.coordinator

    async def transcribe(audio):
        return audio, Transcript("Licht aus", "de", 0.0, 0.0)

    async def handle_text(*args, **kwargs):
        return "", []

    coordinator._transcribe = transcribe
    coordinator.handle_text = handle_text

    async def go():
        await coordinator.on_start(mic, "conv", "jarvis")
        coordinator.on_audio(mic, tone(0.3, 30))
        coordinator.on_audio(mic, tone(0.6, 9000))
        coordinator.on_audio(mic, tone(1.0, 20))
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert "STT_VAD_END" not in events
    assert events.count("RUN_END") == 1
    assert mic.id not in coordinator.runs


def test_a_new_command_while_thinking_discards_the_old_answer(house):
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    record_events(house)
    coordinator = house.coordinator
    started = asyncio.Event()
    handled = []

    async def handle_text(session, area_, text, lang, person_id, origin="", followup=False, hold=None):
        handled.append((text, followup))
        if text == "alt":
            started.set()
            await asyncio.sleep(0.3)
            return "alte Antwort", []
        return "neue Antwort", []

    async def say(text, lang, areas):
        handled.append(("say", text))
        return True

    coordinator.handle_text = handle_text
    house.speech.say = say

    async def scenario():
        from jarvis.voice.coordinator import Run
        from jarvis.voice.vad import Endpointer

        async def transcribe(audio):
            return audio, Transcript("alt", "de", 0.0, 0.0)

        coordinator._transcribe = transcribe
        run = Run(mic, area.id, "command", Endpointer(), gen=0)
        task = asyncio.create_task(coordinator._process(run, b"\0" * 4000))
        await started.wait()
        assert await coordinator.on_start(mic, "conv", "jarvis") == 0
        await task
        return task

    asyncio.run(scenario())
    assert ("say", "alte Antwort") not in handled
    assert coordinator.session(area.id).phase == Phase.LISTENING


def test_a_followup_that_starts_with_jarvis_is_a_new_command(house):
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    coordinator = house.coordinator
    seen = {}

    async def handle_text(session, area_, text, lang, person_id, origin="", followup=False, hold=None):
        seen["text"] = text
        seen["followup"] = followup
        return "Erledigt.", []

    async def say(*a, **k):
        return True

    async def transcribe(audio):
        return audio, Transcript("Jarvis, Licht aus", "de", 0.0, 0.0)

    coordinator.handle_text = handle_text
    coordinator._transcribe = transcribe
    house.speech.say = say
    house.mics.listen = lambda mic_: _done()

    async def go():
        from jarvis.voice.coordinator import Run
        from jarvis.voice.vad import Endpointer

        run = Run(mic, area.id, "followup", Endpointer(), gen=0)
        await coordinator._process(run, b"\0" * 4000)

    asyncio.run(go())
    assert seen == {"text": "Licht aus", "followup": False}


def _done():
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(True)
    return fut


def test_saying_jarvis_while_he_speaks_interrupts_him(house):
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    record_events(house)
    coordinator = house.coordinator
    session = coordinator.session(area.id)

    async def go():
        cut = asyncio.Event()
        house.speech._cuts[area.id] = cut
        coordinator.speaking.add(area.id)
        session.phase = Phase.SPEAKING
        assert await coordinator.on_start(mic, "conv", None) is None, "no wake word, no interruption"
        assert not cut.is_set()
        assert await coordinator.on_start(mic, "conv", "jarvis") == 0
        return cut

    cut = asyncio.run(go())
    assert cut.is_set()
    assert coordinator.runs[mic.id].state == "listening"
    assert session.phase == Phase.LISTENING


def test_an_interrupted_answer_stops_waiting_and_tries_to_end_the_announcement(house):
    area = house.areas.create("Wohnen")
    announcement = Announcement("jarvis.wav", {5})
    cuts = []

    class Zone:
        details = {"playerid": 5}

    async def render(text, lang, lead_ms=None):
        return wav.Pcm(np.zeros(24000 * 8, dtype=np.int16), 24000)

    async def announce(targets, data):
        announcement.began.set()
        return announcement

    async def cut(target):
        cuts.append(target)

    house.zone_of = lambda a: Zone()
    house.speech.render = render
    house.audioserver.announce = announce
    house.audioserver.cut = cut

    async def go():
        task = asyncio.create_task(house.speech.say("Eine lange Antwort.", "de", [area]))
        await asyncio.sleep(0.05)
        assert house.speech.interrupt(area.id)
        await asyncio.wait_for(task, timeout=1.0)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert cuts == [announcement]
    assert area.id not in house.coordinator.speaking


def test_boards_keep_listening_while_jarvis_talks_unless_he_says_jarvis(house):
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    coordinator = house.coordinator
    coordinator.set_speaking(area.id, True, "Das Licht ist an, Sir.")
    assert mic.client.switch == [True], "wake word stays on"
    assert mic.client.number[-1] == 65, "Jarvis stays easy to interrupt over the announcement"
    coordinator.set_speaking(area.id, False)
    assert mic.client.number[-1] == 50
    coordinator.set_speaking(area.id, True, "Ich bin Jarvis, Ihr Butler.")
    assert mic.client.switch[-1] is False, "his own name would wake the board"
    coordinator.set_speaking(area.id, False)
    assert mic.client.switch[-1] is True


def test_a_lost_board_does_not_block_the_bereich(house):
    area = house.areas.create("Wohnen")
    mic = board(house, area.id)
    record_events(house)
    coordinator = house.coordinator

    async def go():
        await coordinator.on_start(mic, "conv", "jarvis")
        coordinator.runs.pop(mic.id)
        return await coordinator.on_start(mic, "conv", "jarvis")

    assert asyncio.run(go()) == 0, "a Bereich stuck in listening starts over"


def test_hush_words_end_the_conversation_quietly():
    for text in ("Stopp", "Stop.", "Jarvis, stopp", "Danke", "Schon gut", "Ok, danke", "Hör auf", "Das reicht"):
        assert _hush(text), text
    for text in ("Stopp die Musik", "Mach das Licht aus", "Halt die Rollos an"):
        assert not _hush(text), text


def test_soft_syllables_do_not_end_the_sentence():
    ep = Endpointer(VadConfig(end_silence_ms=300))
    ep.feed(tone(0.3, 30))
    assert ep.feed(tone(0.3, 3000)) == "continue"
    loud = Endpointer(VadConfig())._threshold()
    quieter = 10 ** ((loud - 2) / 20) * 32768 * np.sqrt(2)
    assert ep.feed(tone(0.6, quieter)) == "continue", "a few dB under the start level is still speech"
    assert ep.feed(tone(0.4, 20)) == "end"


def test_the_sentence_limit_counts_from_the_first_word():
    ep = Endpointer(VadConfig(no_speech_timeout_ms=8000, max_utterance_ms=2000))
    assert ep.feed(tone(3.0, 20)) == "continue", "a late start keeps its full time"
    assert ep.feed(tone(1.5, 9000)) == "continue"
    assert ep.feed(tone(0.6, 9000)) == "end"


class Server:
    def __init__(self, music):
        self.music = music
        self.paused = []
        self.played = []

    def music_playing(self, player):
        return self.music.get(player, False)

    async def pause(self, player):
        self.paused.append(player)

    async def play(self, player):
        self.played.append(player)


def test_cutting_an_announcement_pauses_every_zone_and_resumes_music(house):
    servers = Audioservers(house.settings, house.miniserver, house.events)
    server = Server({1: True})
    servers.client_for = lambda player: server
    announcement = Announcement("jarvis.wav", {1, 2})

    async def go():
        async def end():
            await asyncio.sleep(0.05)
            announcement.finished.set()

        asyncio.create_task(end())
        return await servers.cut(announcement, wait=0.5)

    assert asyncio.run(go())
    assert server.paused == [1, 2]
    assert server.played == [1]
