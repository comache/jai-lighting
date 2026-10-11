"""Native scenes activate absolute inherited presets through one leaf plan."""

from homeassistant.components.scene import Scene
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .presets import resolve


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([JaiScene(entry)])


class JaiScene(Scene):
    _attr_should_poll = False

    def __init__(self, entry):
        self.manager = entry.runtime_data
        self.preset_id = entry.entry_id
        self._attr_unique_id = self.preset_id
        self._attr_name = self.manager.snapshot.presets[self.preset_id].name
        self.entity_id = f"scene.jai_{slugify(self._attr_name)}"

    @property
    def available(self):
        effective = resolve(
            self.manager.snapshot.tree,
            self.manager.snapshot.presets[self.preset_id],
            collections=self.manager.snapshot.collections,
        )
        observations = self.manager.observations()
        return any(observations[id_].available for id_ in effective.values)

    @property
    def extra_state_attributes(self):
        return {
            "jai_preset": True,
            "jai_metadata_version": 1,
            "preset_id": self.preset_id,
            "target_ids": [t.id for t in self.manager.snapshot.presets[self.preset_id].targets],
            "recipe_revision": self.manager.snapshot.config_revision,
            "last_operation_status": self.manager.outcome.get("status"),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        key = f"preset:{self.preset_id}"
        self.manager.listeners[key] = self._updated
        self.async_on_remove(lambda: self.manager.listeners.pop(key, None))

    def _updated(self):
        self._attr_name = self.manager.snapshot.presets[self.preset_id].name
        entry = er.async_get(self.hass).async_get(self.entity_id)
        if entry and entry.original_name != self._attr_name:
            er.async_get(self.hass).async_update_entity(
                self.entity_id, original_name=self._attr_name
            )
        self.async_write_ha_state()

    async def async_activate(self, **kwargs):
        if kwargs:
            raise HomeAssistantError(
                "J'ai Lighting presets do not support transitions or extra scene properties"
            )
        await self.manager.execute("preset", self._context, preset_id=self.preset_id)
