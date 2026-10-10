"""UI configuration and membership editing for relative lighting groups."""

from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_MEMBERS, DOMAIN
from .membership import is_aggregate, validate_members


def group_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    """Offer actual lights and hide known aggregates from the selector."""
    candidates = set(hass.states.async_entity_ids("light")) | {
        entry.entity_id for entry in er.async_get(hass).entities.values() if entry.domain == "light"
    }
    excluded = [entity_id for entity_id in candidates if is_aggregate(hass, entity_id)]
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
            vol.Required(
                CONF_MEMBERS, default=defaults.get(CONF_MEMBERS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    filter={"domain": "light"}, multiple=True, exclude_entities=excluded
                )
            ),
        }
    )


def validate(
    hass: HomeAssistant, data: dict[str, Any], existing: list[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    name = data[CONF_NAME].strip()
    if not name:
        return data, {CONF_NAME: "name_required"}
    members, error = validate_members(hass, data[CONF_MEMBERS], existing)
    if error:
        return data, {CONF_MEMBERS: error}
    return {CONF_NAME: name, CONF_MEMBERS: members}, {}


class JaiConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per group; repeated creation supports multiple groups."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return JaiOptionsFlow()

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            data, errors = validate(self.hass, user_input, [])
            if not errors:
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="user", data_schema=group_schema(self.hass, user_input or {}), errors=errors
        )


class JaiOptionsFlow(OptionsFlow):
    """Change membership/name without changing virtual entity identity."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        current = dict(self.config_entry.options or self.config_entry.data)
        errors = {}
        if user_input is not None:
            data, errors = validate(self.hass, user_input, current[CONF_MEMBERS])
            if not errors:
                return self.async_create_entry(title="", data=data)
        return self.async_show_form(
            step_id="init",
            data_schema=group_schema(self.hass, user_input or current),
            errors=errors,
        )
