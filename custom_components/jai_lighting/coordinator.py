"""Event-driven Home Assistant adapter for the relative fader engine."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, brightness_supported
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .const import SERVICE_TIMEOUT, STATE_TIMEOUT
from .engine import Command, FaderEngine, Member
from .membership import is_aggregate

_LOGGER = logging.getLogger(__name__)


class GroupCoordinator:
    """Read actual HA state, serialize groups, and acknowledge physical calls."""

    def __init__(self, hass: HomeAssistant, members: list[str], lock: asyncio.Lock) -> None:
        self.hass = hass
        self.engine = FaderEngine(members)
        self.lock = lock
        self.failures: dict[str, str] = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: Callable[[], None] | None = None

    def observations(self) -> dict[str, Member]:
        """Map each unique leaf to its actual reported state."""
        observations = {}
        for entity_id in self.engine.members:
            state = self.hass.states.get(entity_id)
            if state is None or is_aggregate(self.hass, entity_id):
                observations[entity_id] = Member(False)
                continue
            modes = state.attributes.get("supported_color_modes") or []
            brightness = state.attributes.get(ATTR_BRIGHTNESS)
            valid_brightness = type(brightness) is int and 1 <= brightness <= 255
            observations[entity_id] = Member(
                available=state.state in (STATE_ON, STATE_OFF),
                on=state.state == STATE_ON,
                dimmable=brightness_supported(set(modes)),
                brightness=brightness if valid_brightness else None,
            )
        return observations

    @property
    def available(self) -> bool:
        return any(member.available for member in self.observations().values())

    @property
    def is_on(self) -> bool:
        return any(member.available and member.on for member in self.observations().values())

    @callback
    def start(self, listener: Callable[[], None]) -> None:
        """Track only configured members; no polling or feedback writes."""
        self._listeners.append(listener)
        self._unsubscribe = async_track_state_change_event(
            self.hass, self.engine.members, self._state_changed
        )
        self.engine.observe(self.observations())

    @callback
    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._listeners.clear()

    @callback
    def _state_changed(self, event: Event) -> None:
        self.engine.observe(self.observations())
        self._notify()

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    async def execute(self, operation: str, context: Context, master: int | None = None) -> None:
        """Plan from fresh actual state and finish partial batches consistently."""
        async with self.lock:
            states = self.observations()
            if operation == "off":
                commands = self.engine.turn_off(states)
            elif operation == "on":
                commands = self.engine.turn_on(states, master)
            else:
                assert master is not None
                commands = self.engine.set_master(master, states)
            self.failures = {}
            self._notify()
            results = await asyncio.gather(
                *(self._send(command, context) for command in commands),
                return_exceptions=True,
            )
            for command, result in zip(commands, results, strict=True):
                if isinstance(result, Exception):
                    self.failures[command.entity_id] = str(result) or type(result).__name__
                    _LOGGER.warning("Command failed for %s: %s", command.entity_id, result)
            self.engine.observe(self.observations())
            self._notify()
            if self.failures:
                raise HomeAssistantError(
                    "J'ai Lighting could not confirm: " + ", ".join(self.failures)
                )

    async def _send(self, command: Command, context: Context) -> None:
        """Wait for a changed real report, accepting device quantization."""
        before = self.hass.states.get(command.entity_id)
        acknowledged = asyncio.Event()

        def matches(state: Any) -> bool:
            if state is None or state.state != (STATE_ON if command.on else STATE_OFF):
                return False
            if command.brightness is None:
                return True
            brightness = state.attributes.get(ATTR_BRIGHTNESS)
            if type(brightness) is not int or not 1 <= brightness <= 255:
                return False
            return brightness == command.brightness or (
                before is not None
                and (state.state, brightness)
                != (before.state, before.attributes.get(ATTR_BRIGHTNESS))
            )

        @callback
        def changed(event: Event) -> None:
            if matches(event.data.get("new_state")):
                acknowledged.set()

        unsubscribe = async_track_state_change_event(self.hass, [command.entity_id], changed)
        data: dict[str, Any] = {"entity_id": command.entity_id}
        if command.brightness is not None:
            data[ATTR_BRIGHTNESS] = command.brightness
        try:
            await asyncio.wait_for(
                self.hass.services.async_call(
                    "light",
                    "turn_on" if command.on else "turn_off",
                    data,
                    blocking=True,
                    context=context,
                ),
                timeout=SERVICE_TIMEOUT,
            )
            if matches(self.hass.states.get(command.entity_id)):
                acknowledged.set()
            await asyncio.wait_for(acknowledged.wait(), timeout=STATE_TIMEOUT)
        finally:
            unsubscribe()
