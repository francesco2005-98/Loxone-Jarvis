"""System instruction for Gemini: persona, fixed rules, house context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

RULES = """REGELN (nicht verhandelbar)
- Du steuerst das Haus ausschließlich über die Werkzeuge. Erfinde keine Geräte, Räume, Szenen oder Zustände.
- Geräte sprichst du nur über ihre Kennung aus der Geräteliste an (z. B. L3). Andere Kennungen gibt es nicht.
- Die Sprechweisen ("auch genannt") sind gleichwertige Namen für dasselbe Gerät.
- Ohne genannten Raum gilt der Bereich, in dem gesprochen wurde. "Licht an" ohne Raum meint das Hauptlicht dieses Bereichs.
- Nennt jemand einen anderen Raum oder Bereich, schalte dort. Deine Antwort wird trotzdem hier gesprochen.
- Passt ein Satz auf mehrere Leuchten und keine davon ist das Hauptlicht, frag in einem kurzen Satz nach, welche gemeint ist.
- Meldet ein Werkzeug "needs_confirmation", stelle genau eine kurze Ja/Nein-Frage, ob du es tun sollst, und tu sonst nichts.
- Für Fragen außerhalb der Haussteuerung (Wissen, Nachrichten, Wetter, Sport, Rechnen mit Fakten) rufe web_search auf.
- Für neue Logik, neue Bausteine oder Programmänderungen in Loxone Config rufe loxone_config auf, falls vorhanden.
- Merke dir Vorlieben nur, wenn jemand ausdrücklich darum bittet ("merk dir ...").
- Nach einer Schaltung reicht eine knappe Bestätigung. Wiederhole keine Kennungen wie L3 in der Antwort.
- Bei control, music, timer (set), announce, remember und rename schreibst du die gesprochene Bestätigung gleich in reply. Klappt alles, wird genau dieser Satz gesprochen und es gibt keine zweite Runde.
- Musikwünsche wie „spiel etwas Jazz im Wohnzimmer“ gehen an music mit action search, value nur dem Suchbegriff (Jazz) und areas dem genannten Bereich. Das startet eine Spotify-Playlist über den Audioserver. Steht der Wunsch wörtlich in den Musik-Favoriten, nimm action favorite.
- „Radio“, „Lautsprecher“ oder „Musik“ in einem Raum meint die Audio-Zone dort (music mit areas = Raum oder Zonenname, action play). Prüfe die Liste der Audio-Zonen, bevor du sagst, dass es keine gibt.
- Antworte in der Sprache, in der du angesprochen wurdest."""


@dataclass
class SpeakerInfo:
    anrede: str | None
    name: str
    notes: str


def build_system(
    *,
    persona: str,
    now: datetime,
    area_name: str,
    area_rooms: list[str],
    main_light: str | None,
    speaker: SpeakerInfo | None,
    household_notes: str,
    devices: list[str],
    areas: list[str],
    favorites: list[str],
    config_available: bool,
    location: str | None = None,
    zones: list[str] | None = None,
    default_anrede: str | None = None,
    states: list[str] | None = None,
) -> str:
    """Stable parts first, changing parts last: Gemini reuses the cached prefix and answers faster."""
    parts = [persona.strip(), "", RULES, ""]
    parts.append("BEREICHE (für Musik, Durchsagen, Hauptlicht): " + (", ".join(areas) if areas else "keine"))
    parts.append(
        "AUDIO-ZONEN IN LOXONE (für music und announce direkt nutzbar, auch ohne Bereich): "
        + (", ".join(zones) if zones else "keine")
    )
    parts.append("")
    parts.append("GERÄTELISTE (Kennung | Name | Raum | Art | auch genannt | Aktionen | Hinweise):")
    parts.extend(devices if devices else ["(keine Geräte freigegeben)"])
    if not config_available:
        parts.append("")
        parts.append("Loxone Config ist nicht verbunden: Programmänderungen sind gerade nicht möglich.")
    if household_notes.strip():
        parts.append("")
        parts.append(f"NOTIZEN DES HAUSHALTS: {household_notes.strip()}")

    parts.append("")
    when = f"{WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y %H:%M} ({now.tzinfo})"
    parts.append(f"JETZT: {when}")
    if location:
        parts.append(
            f"STANDORT DES HAUSES: {location}. Wetter, Sonne, Öffnungszeiten, Verkehr und „hier in der Nähe“ "
            "beziehen sich auf diesen Ort, wenn kein anderer genannt wird."
        )
    rooms = ", ".join(area_rooms) if area_rooms else "keine zugeordnet"
    parts.append(f"BEREICH: {area_name} (Loxone-Räume: {rooms}). Hauptlicht: {main_light or 'nicht festgelegt'}.")
    if speaker and speaker.anrede:
        parts.append(
            f"SPRECHER: {speaker.name}. Sprich die Person mit \"{speaker.anrede}\" an, natürlich und nicht in jedem Satz."
        )
        if speaker.notes:
            parts.append(f"NOTIZEN ZU {speaker.name}: {speaker.notes}")
    elif default_anrede:
        parts.append(
            f"SPRECHER: nicht sicher erkannt. Sprich die Person mit \"{default_anrede}\" an, sparsam und natürlich, "
            "nicht in jedem Satz. Keinen Namen verwenden, nicht fragen, wer spricht."
        )
    else:
        parts.append(
            "SPRECHER: nicht sicher erkannt. Verwende keinen Namen und keine Anrede wie Sir oder Ma'am. "
            "Frag nicht, wer spricht."
        )
    if favorites:
        parts.append("MUSIK-FAVORITEN HIER: " + ", ".join(favorites))
    if states:
        parts.append("AKTUELLE ZUSTÄNDE (Kennung: Zustand):")
        parts.extend(states)
    return "\n".join(parts)
