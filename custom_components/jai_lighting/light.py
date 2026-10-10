"""Normal LightEntity controls plus a power-preserving master action."""

import math
from typing import Any

import probatio as vol
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import slugify

from . import JaiConfigEntry
from .engine import level


def finite_percentage(value: float) -> float:
    """Reject NaN/Infinity as well as out-of-range master input."""
    if not math.isfinite(value):
        raise vol.Invalid("Master brightness must be a finite percentage")
    return value


class FaderStoredData(ExtraStoredData):
    """Extra restoration data independent of displayed on/off state."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def as_dict(self) -> dict[str, Any]:
        return self.data


async def async_setup_entry(
    hass: HomeAssistant, entry: JaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create one stable light per group and register a targeted entity action."""
    async_add_entities([JaiLight(entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_master",
        {
            vol.Required("brightness_pct"): vol.All(
                vol.Coerce(float), finite_percentage, vol.Range(min=1, max=100)
            )
        },
        "async_set_master",
    )


class JaiLight(LightEntity, RestoreEntity):
    """Brightness is the independent master, not aggregate member brightness."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_icon = "mdi:lightbulb-group"

    def __init__(self, entry: JaiConfigEntry) -> None:
        self.coordinator = entry.runtime_data
        config = entry.options or entry.data
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self.entity_id = f"light.jai_{slugify(config[CONF_NAME])}"

    @property
    def brightness(self) -> int:
        return self.coordinator.engine.master

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_on

    @property
    def available(self) -> bool:
        return self.coordinator.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "jai_group": True,
            "entity_id": list(self.coordinator.engine.members),
            "master_brightness": self.brightness,
            "master_brightness_pct": round(self.brightness / 255 * 100, 2),
            "unavailable_members": [
                entity_id
                for entity_id, member in self.coordinator.observations().items()
                if not member.available
            ],
            "command_failures": dict(self.coordinator.failures),
        }

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return FaderStoredData(self.coordinator.engine.as_dict())

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_extra_data()) is not None:
            self.coordinator.engine.restore(stored.as_dict())
        self.coordinator.start(self.async_write_ha_state)
        self.async_on_remove(self.coordinator.stop)

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness == 0:
            await self.async_turn_off()
            return
        await self.coordinator.execute("on", self._context, brightness)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.execute("off", self._context)

    async def async_set_master(self, brightness_pct: float) -> None:
        await self.coordinator.execute(
            "master", self._context, level(brightness_pct * 255 / 100, 3)
        )
