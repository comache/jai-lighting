"""Pure, bounded relative-fader state and command planning."""

import math
from dataclasses import dataclass
from typing import Any

from .const import MAX_BRIGHTNESS, MIN_MASTER, RESTORE_VERSION


def level(value: float, minimum: int = 1) -> int:
    """Round half up and clamp to a real HA brightness byte."""
    return max(minimum, min(MAX_BRIGHTNESS, math.floor(value + 0.5)))


@dataclass(frozen=True)
class Member:
    """Current physical state; None brightness is not an invented baseline."""

    available: bool
    on: bool = False
    dimmable: bool = False
    brightness: int | None = None


@dataclass(frozen=True)
class Command:
    """One physical leaf command."""

    entity_id: str
    on: bool
    brightness: int | None = None


class FaderEngine:
    """Master value plus clipped last-on/off-restore brightness memory."""

    def __init__(self, members: list[str]) -> None:
        self.members = tuple(dict.fromkeys(members))
        self.master = MAX_BRIGHTNESS
        self.memory: dict[str, int] = {}

    def observe(self, states: dict[str, Member]) -> None:
        """Authoritative on observations supersede saved restore targets."""
        for entity_id in self.members:
            member = states[entity_id]
            if member.available and member.on and member.brightness is not None:
                self.memory[entity_id] = level(member.brightness)

    def _remember_off(self, states: dict[str, Member]) -> None:
        """Bootstrap missing restore targets, never overwrite pending targets."""
        for entity_id in self.members:
            member = states[entity_id]
            if member.dimmable and entity_id not in self.memory:
                self.memory[entity_id] = level(
                    member.brightness if member.brightness is not None else 255
                )

    def set_master(self, value: int, states: dict[str, Member]) -> list[Command]:
        """Plan a relative movement, preserving current member power state."""
        target = level(value, MIN_MASTER)
        self.observe(states)
        if target == self.master:
            return []
        factor = target / self.master
        self.master = target
        if not any(member.available and member.on for member in states.values()):
            self._remember_off(states)
            self.memory = {
                entity_id: level(brightness * factor)
                for entity_id, brightness in self.memory.items()
            }
            return []
        commands = []
        for entity_id in self.members:
            member = states[entity_id]
            if member.available and member.on and member.dimmable and member.brightness is not None:
                brightness = level(member.brightness * factor)
                if brightness != member.brightness:
                    commands.append(Command(entity_id, True, brightness))
        return commands

    def turn_off(self, states: dict[str, Member]) -> list[Command]:
        """Capture real values, then switch every available member off."""
        self.observe(states)
        self._remember_off(states)
        return [
            Command(entity_id, False) for entity_id in self.members if states[entity_id].available
        ]

    def turn_on(self, states: dict[str, Member], master: int | None = None) -> list[Command]:
        """Optionally move the fader, then power on every available member."""
        commands = self.set_master(master, states) if master is not None else []
        self.observe(states)
        self._remember_off(states)
        commands.extend(
            Command(
                entity_id,
                True,
                self.memory.get(entity_id, 255) if states[entity_id].dimmable else None,
            )
            for entity_id in self.members
            if states[entity_id].available and not states[entity_id].on
        )
        return commands

    def as_dict(self) -> dict[str, Any]:
        """Serializable bounded data, including master when entity is off."""
        return {
            "version": RESTORE_VERSION,
            "master": self.master,
            "memory": dict(self.memory),
        }

    def restore(self, data: dict[str, Any]) -> None:
        """Validate storage, prune removed members and reject invalid values."""
        if data.get("version") != RESTORE_VERSION:
            return
        master = data.get("master")
        if type(master) is int and MIN_MASTER <= master <= 255:
            self.master = master
        memory = data.get("memory")
        if isinstance(memory, dict):
            self.memory = {
                entity_id: value
                for entity_id, value in memory.items()
                if entity_id in self.members and type(value) is int and 1 <= value <= 255
            }
