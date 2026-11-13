"""Flatten LoxAPP3.json into controls Jarvis can reason about."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Door openers live under these types. The whole subtree stays out of Jarvis.
BLOCKED_TREES = {"Intercom", "IntercomV2", "NfcCodeTouch", "Webpage"}


@dataclass
class LoxControl:
    uuid: str
    name: str
    type: str
    room: str | None
    room_uuid: str | None
    category: str | None
    cat_type: str | None
    parent: str | None
    states: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    secured: bool = False
    children: list[str] = field(default_factory=list)


@dataclass
class Structure:
    ms_name: str = ""
    location: str = ""
    serial: str = ""
    last_modified: str = ""
    rooms: dict[str, str] = field(default_factory=dict)
    controls: dict[str, LoxControl] = field(default_factory=dict)
    state_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    media_servers: dict[str, dict] = field(default_factory=dict)

    def zones(self) -> list[LoxControl]:
        return [c for c in self.controls.values() if c.type == "AudioZoneV2"]

    def audioservers(self) -> list[dict]:
        """Every Audioserver the zones use or the Miniserver lists: uuid, name and host without port."""
        used = {str(zone.details.get("server", "")) for zone in self.zones()}
        found: list[dict] = []
        seen: set[str] = set()
        for uuid, server in self.media_servers.items():
            host = strip_port(str(server.get("host") or ""))
            if not host or host in seen or (uuid not in used and server.get("type") != 2):
                continue
            seen.add(host)
            found.append({"uuid": uuid, "name": str(server.get("name") or host), "host": host})
        return found

    def audioserver_host(self) -> str | None:
        """Host of the Audioserver that serves the first audio zone."""
        for zone in self.zones():
            host = self.zone_server_host(zone)
            if host:
                return host
        servers = self.audioservers()
        return servers[0]["host"] if servers else None

    def zone_server_host(self, zone: LoxControl) -> str | None:
        server = self.media_servers.get(str(zone.details.get("server", "")))
        host = strip_port(str(server.get("host") or "")) if server else ""
        return host or None

    def zone_for_player(self, player: int) -> LoxControl | None:
        for zone in self.zones():
            if str(zone.details.get("playerid")) == str(player):
                return zone
        return None

    def child(self, parent_uuid: str, suffix: str) -> LoxControl | None:
        for child_uuid in self.controls.get(parent_uuid, LoxControl("", "", "", None, None, None, None, None)).children:
            if child_uuid.endswith(suffix):
                return self.controls.get(child_uuid)
        return None


def strip_port(host: str) -> str:
    """'192.168.1.40:7091' -> '192.168.1.40'. Bracketed IPv6 keeps its address."""
    host = host.strip()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _states(raw: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in (raw or {}).items():
        if isinstance(value, list):
            if value:
                out[name] = str(value[0])
        elif isinstance(value, str):
            out[name] = value
    return out


def parse(data: dict) -> Structure:
    info = data.get("msInfo", {}) or {}
    rooms = {uuid: (room or {}).get("name", "") for uuid, room in (data.get("rooms") or {}).items()}
    cats = data.get("cats") or {}
    structure = Structure(
        ms_name=str(info.get("msName", "")),
        location=str(info.get("location", "") or ""),
        serial=str(info.get("serialNr", "")),
        last_modified=str(data.get("lastModified", "")),
        rooms=rooms,
        media_servers={k: v for k, v in (data.get("mediaServer") or {}).items() if isinstance(v, dict)},
    )

    def add(raw: dict, parent: LoxControl | None) -> None:
        ctype = str(raw.get("type", ""))
        if ctype in BLOCKED_TREES:
            return
        uuid = str(raw.get("uuidAction") or "")
        if not uuid:
            return
        room_uuid = raw.get("room") or (parent.room_uuid if parent else None)
        cat_uuid = raw.get("cat")
        cat = cats.get(cat_uuid) if cat_uuid else None
        control = LoxControl(
            uuid=uuid,
            name=str(raw.get("name", "")).strip(),
            type=ctype,
            room=rooms.get(room_uuid) if room_uuid else None,
            room_uuid=room_uuid,
            category=(cat or {}).get("name") if cat else (parent.category if parent else None),
            cat_type=(cat or {}).get("type") if cat else (parent.cat_type if parent else None),
            parent=parent.uuid if parent else None,
            states=_states(raw.get("states")),
            details=raw.get("details") or {},
            secured=bool(raw.get("isSecured")),
        )
        structure.controls[uuid] = control
        if parent:
            parent.children.append(uuid)
        for name, state_uuid in control.states.items():
            structure.state_index[state_uuid] = (uuid, name)
        for sub in (raw.get("subControls") or {}).values():
            add(sub, control)

    for raw in (data.get("controls") or {}).values():
        add(raw, None)
    return structure
