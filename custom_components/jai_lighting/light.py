"""Hierarchical LightEntity: independent fader and descendant capabilities."""

import math
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import slugify

from .capabilities import aggregate, properties
from .engine import level
from .services import finite_percentage


class FaderStoredData(ExtraStoredData):
    def __init__(self, data):
        self.data = data

    def as_dict(self):
        return self.data


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([JaiLight(entry)])


class JaiLight(LightEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:lightbulb-group"

    def __init__(self, entry):
        self.coordinator = entry.runtime_data
        self.manager = self.coordinator.manager
        self.group_id = entry.entry_id
        self._attr_name = self.manager.snapshot.tree.groups[self.group_id].name
        self._attr_unique_id = entry.entry_id
        self.entity_id = f"light.jai_{slugify(self._attr_name)}"
        self.axis = None

    def _observations(self):
        ids = self.manager.snapshot.tree.resolve(self.group_id)
        observations = self.manager.observations()
        return {id_: observations[id_] for id_ in ids}

    def _caps(self):
        return aggregate([o.capabilities for o in self._observations().values()])

    @property
    def supported_color_modes(self):
        return {ColorMode(mode) for mode in self._caps()["modes"]}

    @property
    def color_mode(self):
        modes = self.supported_color_modes
        if self.axis in modes:
            return self.axis
        return next(
            mode
            for mode in (ColorMode.HS, ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS, ColorMode.ONOFF)
            if mode in modes
        )

    @property
    def supported_features(self):
        return LightEntityFeature.EFFECT if self.effect_list else LightEntityFeature(0)

    @property
    def effect_list(self):
        return self._caps()["effects"] or None

    @property
    def min_color_temp_kelvin(self):
        return self._caps()["min_kelvin"]

    @property
    def max_color_temp_kelvin(self):
        return self._caps()["max_kelvin"]

    def _uniform(self, key, capability):
        values = []
        for o in self._observations().values():
            eligible = (
                o.capabilities.color
                if capability == "color"
                else o.capabilities.temperature
                if capability == "ct"
                else bool(o.capabilities.effects)
            )
            if o.available and o.on and eligible:
                value = o.properties.get(key)
                if key == "hs_color" and (
                    not isinstance(value, (tuple, list))
                    or len(value) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
                ):
                    value = None
                if key == "color_temp_kelvin" and type(value) is not int:
                    value = None
                values.append(value)
        if not values or any(v is None for v in values):
            return None, "unknown"
        first = values[0]
        for value in values[1:]:
            if key == "hs_color":
                same = min(abs(first[0] - value[0]), 360 - abs(first[0] - value[0])) <= 1 and (
                    abs(first[1] - value[1]) <= 1
                )
            elif key == "color_temp_kelvin":
                same = abs(first - value) <= 1
            else:
                same = first == value
            if not same:
                return None, "mixed"
        return first, "uniform"

    @property
    def hs_color(self):
        return self._uniform("hs_color", "color")[0]

    @property
    def color_temp_kelvin(self):
        return self._uniform("color_temp_kelvin", "ct")[0]

    @property
    def effect(self):
        return self._uniform("effect", "effect")[0]

    @property
    def brightness(self):
        return self.coordinator.engine.master

    @property
    def is_on(self):
        return self.coordinator.is_on

    @property
    def available(self):
        return self.coordinator.available

    @property
    def extra_state_attributes(self):
        tree = self.manager.snapshot.tree
        group = tree.groups[self.group_id]
        registry = er.async_get(self.hass)

        def entity_for(id_):
            return registry.async_get_entity_id("light", "jai_lighting", id_)

        children = tree.children.get(self.group_id, ())
        routes = self.manager.routes()
        direct = tree.members.get(self.group_id, ())
        return {
            "jai_group": True,
            "jai_metadata_version": 1,
            "group_id": self.group_id,
            "parent_group_id": group.parent,
            "parent_entity_id": entity_for(group.parent) if group.parent else None,
            "child_group_ids": list(children),
            "child_entity_ids": [entity_for(c) for c in children],
            "direct_leaf_ids": list(direct),
            "direct_member_entity_ids": [routes[id_] for id_ in direct],
            "entity_id": [
                route for route in self.coordinator.engine.members if route.startswith("light.")
            ],
            "descendant_leaf_count": len(tree.resolve(self.group_id)),
            "depth": len(tree.ancestors(self.group_id)) - 1,
            "sibling_order": group.order,
            "power_policy": group.policy.value,
            "master_brightness": self.brightness,
            "master_brightness_pct": round(self.brightness / 255 * 100, 2),
            "capability_coverage": self._caps()["coverage"],
            "color_state_status": self._uniform("hs_color", "color")[1],
            "ct_state_status": self._uniform("color_temp_kelvin", "ct")[1],
            "config_revision": self.manager.snapshot.config_revision,
            "last_operation_status": self.manager.outcome.get("status"),
            "unavailable_members": [
                routes[id_] or id_ for id_, o in self._observations().items() if not o.available
            ],
            "command_failures": dict(self.manager.failures),
        }

    @property
    def extra_restore_state_data(self):
        return FaderStoredData(self.coordinator.engine.as_dict())

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # The shared Store, including migrated v1 data, is authoritative.
        self.coordinator.start(self._updated)
        self.async_on_remove(self.coordinator.stop)

    def _updated(self):
        name = self.manager.snapshot.tree.groups[self.group_id].name
        self._attr_name = name
        entry = er.async_get(self.hass).async_get(self.entity_id)
        if entry and entry.original_name != name:
            er.async_get(self.hass).async_update_entity(self.entity_id, original_name=name)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any):
        brightness = kwargs.get("brightness")
        if brightness == 0:
            await self.async_turn_off()
            return
        fields = {}
        if "color_temp_kelvin" in kwargs:
            fields["color_temp_kelvin"] = kwargs["color_temp_kelvin"]
            self.axis = ColorMode.COLOR_TEMP
        if "hs_color" in kwargs:
            fields["color"] = {"kind": "hs", "value": list(kwargs["hs_color"])}
            self.axis = ColorMode.HS
        if "effect" in kwargs:
            fields["effect"] = kwargs["effect"]
        await self.coordinator.execute(
            "on", self._context, brightness, properties(fields) if fields else None
        )

    async def async_turn_off(self, **kwargs):
        await self.coordinator.execute("off", self._context)

    async def async_set_master(self, brightness_pct):
        await self.coordinator.execute(
            "master", self._context, level(finite_percentage(brightness_pct) * 255 / 100, 3)
        )
