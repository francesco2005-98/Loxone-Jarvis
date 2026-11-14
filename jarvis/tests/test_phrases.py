import asyncio
import json

from fastapi.testclient import TestClient
from google.genai import types

from jarvis.brain.phrases import DEFAULT, PhraseBook, fill, validate
from jarvis.brain.tools import Turn
from jarvis.voice.arbitration import AreaSession, Phase
from jarvis.voice.vad import VadConfig

H = {"X-Jarvis": "1"}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_fill_handles_empty_room_and_anrede():
    assert fill("Licht {raum} ist an{, anrede}.") == "Licht ist an."
    assert fill("Licht {raum} ist an{, anrede}.", raum="im Esszimmer", anrede="Sir") == "Licht im Esszimmer ist an, Sir."
    assert fill("{raum} wird es hell.", raum="im Bad") == "Im Bad wird es hell."


def test_validate_drops_broken_variants_and_falls_back():
    book = validate(
        {
            "de": {
                "light_on": ["Sehr wohl, Licht {raum} an{, anrede}.", "Licht an.", "Licht {raum} brennt {name}."],
                "dim": ["Auf {wert} Prozent {raum}.", "{wert} Prozent{, anrede}."],
            }
        }
    )
    assert book["de"]["light_on"] == DEFAULT["de"]["light_on"], "only one valid variant left, defaults stay"
    assert book["de"]["dim"] == ["Auf {wert} Prozent {raum}.", "{wert} Prozent{, anrede}."]
    assert book["en"]["time"] == DEFAULT["en"]["time"]


def test_pick_varies_and_never_repeats_twice(tmp_path):
    book = PhraseBook(tmp_path / "phrases.json")
    seen = [book.pick("light_on", "de", raum="im Esszimmer", anrede="Sir") for _ in range(12)]
    assert all("Esszimmer" in s for s in seen)
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert len(set(seen)) > 1


def test_book_is_written_from_the_personality(house, tmp_path):
    persona = "Du bist ein knurriger Pirat."
    reply = {
        lang: {intent: [f"{intent} {lang} {{raum}} {{wert}} {{uhrzeit}} {{datum}} eins", f"{intent} {lang} {{raum}} {{wert}} {{uhrzeit}} {{datum}} zwei"] for intent in DEFAULT[lang]}
        for lang in ("de", "en")
    }
    seen = {}

    async def generate(contents, config):
        seen["system"] = config.system_instruction
        seen["mime"] = config.response_mime_type
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(reply))]))]
        )

    house.brain.generate = generate
    book = PhraseBook(tmp_path / "phrases.json")
    assert not book.current_for(persona)
    run(book.generate(house.brain, persona))
    assert seen == {"system": persona, "mime": "application/json"}
    assert book.current_for(persona)
    again = PhraseBook(tmp_path / "phrases.json")
    assert again.pick("light_on", "de", raum="im Bad").startswith("Light_on de im Bad")


def test_refresh_needs_a_key(house):
    house.secrets.delete("gemini.api_key")
    assert run(house.refresh_phrases(force=True)) is False


def test_tuning_settings_reach_voice_and_brain(house):
    house.settings.update({"voice.end_silence_ms": 900, "brain.thinking": "high", "gemini.model": "gemini-3.8-flash"})
    assert house.coordinator._vad(2).end_silence_ms == 900
    assert VadConfig.for_noise_filter(2).end_silence_ms == 400
    assert house.brain._thinking().thinking_level == types.ThinkingLevel.HIGH
    house.settings.update({"brain.thinking": "minimal", "gemini.model": "gemini-3.8-pro"})
    assert house.brain._thinking().thinking_level == types.ThinkingLevel.LOW, "Pro has no minimal level"
    session = AreaSession("a")
    session.open_followup("mic", 0)
    assert session.phase == Phase.IDLE
    session.open_followup("mic", 5)
    assert session.phase == Phase.FOLLOWUP


def test_instant_answers_can_be_switched_off(house):
    area = house.areas.create("Wohnbereich")
    house.settings.update({"brain.quick": False})
    turn = Turn(area=area, lang="de", text="Wie spät ist es?")
    assert run(house.coordinator._quick(turn)) is None
    house.settings.update({"brain.quick": True})
    assert "Uhr" in run(house.coordinator._quick(turn))


def test_phrase_endpoints(house):
    from jarvis.main import create_app

    client = TestClient(create_app(house), client=("192.168.1.5", 50000))
    client.post("/api/setup", json={"password": "langes-passwort"}, headers=H)
    data = client.get("/api/phrases").json()
    assert len(data["examples"]) == 3 and data["generated_at"] is None
    house.secrets.delete("gemini.api_key")
    assert client.post("/api/phrases/refresh", headers=H).status_code == 400
