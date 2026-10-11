"""Native group/preset editors, inheritance previews and guided legacy ownership."""

from dataclasses import replace
from uuid import uuid4

import probatio as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .capabilities import absolute
from .const import DOMAIN
from .manager import get_manager
from .membership import is_aggregate, resolve_identity
from .migration import OwnershipRequired
from .models import ModelError
from .presets import Preset, Target, resolve
from .storage import encode_properties


def select(options, *, multiple=False):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=multiple)
    )


def number(min_, max_, step=1):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=min_, max=max_, step=step, mode="box")
    )


class Editor:
    """Working drafts remain private to a flow until validated confirmation."""

    async def initialize(self):
        if not hasattr(self, "manager"):
            self.manager = await get_manager(self.hass)
            self.revision = self.manager.snapshot.config_revision
            self.token = uuid4().hex
            self.group_id = None
            self.group_data = None
            self.group_draft = None
            self.draft_presets = dict(self.manager.snapshot.presets)
            self.recipe_id = self.token
            self.recipe_name = ""
            self.rows = []
            self.edit_index = None
            self.repairing = False

    def choices(self, tree=None):
        tree = tree or self.manager.snapshot.tree
        groups = [
            {
                "value": id_,
                "label": " / ".join(tree.groups[g].name for g in tree.ancestors(id_))
                + f" [{id_[-6:]}]",
            }
            for id_ in tree.groups
        ]
        leaves = [
            {
                "value": id_,
                "label": resolve_identity(self.hass, leaf.identity)
                or f"Missing light [{id_[-6:]}]",
            }
            for id_, leaf in tree.leaves.items()
        ]
        return groups, leaves

    def group_schema(self, defaults):
        groups, _ = self.choices()
        groups = [g for g in groups if g["value"] != self.group_id]
        excluded = [
            id_
            for id_ in self.hass.states.async_entity_ids("light")
            if is_aggregate(self.hass, id_)
        ]
        missing = []
        if self.group_id in self.manager.snapshot.tree.groups:
            missing = [
                {"value": id_, "label": f"Missing configured member [{id_[-6:]}]"}
                for id_ in self.manager.snapshot.tree.members.get(self.group_id, ())
                if resolve_identity(self.hass, self.manager.snapshot.tree.leaves[id_].identity)
                is None
            ]
        return vol.Schema(
            {
                vol.Required("name", default=defaults.get("name", "")): selector.TextSelector(),
                # HA's frontend rejects "" for a required selector, including
                # the valid No parent choice. Empty groups/membership are valid.
                vol.Optional("parent", default=defaults.get("parent", "")): select(
                    [{"value": "", "label": "No parent"}] + groups
                ),
                vol.Optional(
                    "members", default=defaults.get("members", [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        filter={"domain": "light"}, multiple=True, exclude_entities=excluded
                    )
                ),
                vol.Optional(
                    "missing_members", default=defaults.get("missing_members", [])
                ): select(missing, multiple=True),
                vol.Optional("children", default=defaults.get("children", [])): select(
                    groups, multiple=True
                ),
                vol.Required(
                    "power_policy", default=defaults.get("power_policy", "RESTORE")
                ): select(["RESTORE", "FULL"]),
                vol.Required("order", default=defaults.get("order", 0)): number(0, 10000),
                vol.Required("allow_moves", default=False): selector.BooleanSelector(),
                vol.Optional("remove_orphans", default=defaults.get("remove_orphans", [])): select(
                    [
                        g
                        for g in groups
                        if self.hass.config_entries.async_get_entry(g["value"]) is None
                    ],
                    multiple=True,
                ),
            }
        )

    async def async_step_group(self, user_input=None):
        await self.initialize()
        errors = {}
        defaults = self.group_data or {}
        if user_input is not None:
            self.group_data = dict(user_input)
            self.group_data["order"] = int(self.group_data.get("order", 0))
            try:
                self.group_draft = self.manager.group_candidate(
                    self.group_id or self.token, self.group_data, presets=self.draft_presets
                )
                return await self.async_step_group_preview()
            except (ModelError, ValueError, TypeError) as err:
                errors["base"] = "invalid_configuration"
                self.error_text = str(err)
        return self.async_show_form(
            step_id="group",
            data_schema=self.group_schema(user_input or defaults),
            errors=errors,
            description_placeholders={"error": getattr(self, "error_text", "")},
        )

    def preview(self, snapshot):
        summaries = []
        for id_, preset in snapshot.presets.items():
            effective = resolve(snapshot.tree, preset)
            old = self.manager.snapshot.presets.get(id_)
            old_effective = resolve(self.manager.snapshot.tree, old) if old else None
            if old_effective == effective:
                continue
            summaries.append(f"Preset: {preset.name}")
            for leaf_id, values in effective.values.items():
                route = (
                    resolve_identity(self.hass, snapshot.tree.leaves[leaf_id].identity) or leaf_id
                )
                old_values = old_effective.values.get(leaf_id, {}) if old_effective else {}
                summaries.append(f"{route}: before {encode_properties(old_values)}")
                for key, value in values.items():
                    source = effective.sources[leaf_id][key]
                    label = (
                        snapshot.tree.groups[source.id].name if source.kind == "group" else route
                    )
                    summaries.append(f"  {key}: {value} — source {label}")
            if old_effective:
                for removed in old_effective.values.keys() - effective.values.keys():
                    summaries.append(f"No longer targeted: {removed}")
            output = absolute(snapshot.runtime, self.manager.observations(), effective.values)
            summaries.append(
                f"Compatible output preview: skips={output.skipped}; CT clamps={output.clamps}"
            )
            for leaf_id, command in output.commands.items():
                summaries.append(f"{leaf_id}: device properties {dict(command.fields)}")
        return "\n".join(summaries) or "No preset effective values change."

    async def async_step_group_preview(self, user_input=None):
        self.group_draft = self.manager.group_candidate(
            self.group_id or self.token, self.group_data, presets=self.draft_presets
        )
        errors = {}
        try:
            self.group_draft.validate()
            summary = self.preview(self.group_draft)
        except ModelError as err:
            summary = str(err)
            errors["base"] = "invalid_preset"
        if user_input is not None:
            action = user_input["action"]
            if action == "edit_group":
                return await self.async_step_group()
            if action == "repair_preset":
                return await self.async_step_repair_preset()
            if not errors:
                try:
                    if self.group_id:
                        hosts = self.hosts(self.group_draft)
                        await self.manager.commit(self.group_draft, hosts, self.revision)
                        return self.async_create_entry(title="", data={})
                    await self.manager.prepare_creation(self.group_draft, self.token, self.revision)
                    return self.async_create_entry(
                        title=self.group_data["name"],
                        data={"kind": "group", "creation_token": self.token},
                    )
                except (ModelError, OSError) as err:
                    summary, errors = str(err), {"base": "configuration_changed"}
        return self.async_show_form(
            step_id="group_preview",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): select(
                        [
                            {"value": "save", "label": "Save"},
                            {"value": "edit_group", "label": "Edit group"},
                            {"value": "repair_preset", "label": "Repair preset"},
                        ]
                    )
                }
            ),
            description_placeholders={"preview": summary},
        )

    def hosts(self, snapshot):
        return [
            {"id": id_, "kind": "group"}
            for id_ in snapshot.tree.groups
            if self.hass.config_entries.async_get_entry(id_)
        ] + [
            {"id": id_, "kind": "preset"}
            for id_ in snapshot.presets
            if self.hass.config_entries.async_get_entry(id_)
        ]

    async def async_step_repair_preset(self, user_input=None):
        if user_input is not None:
            preset = self.draft_presets[user_input["preset"]]
            self.recipe_id, self.recipe_name = preset.id, preset.name
            self.rows, self.repairing = list(preset.targets), True
            return await self.async_step_recipe()
        return self.async_show_form(
            step_id="repair_preset",
            data_schema=vol.Schema(
                {
                    vol.Required("preset"): select(
                        [{"value": id_, "label": p.name} for id_, p in self.draft_presets.items()]
                    )
                }
            ),
        )

    async def async_step_preset(self, user_input=None):
        await self.initialize()
        errors = {}
        if user_input is not None:
            self.recipe_name = user_input["name"].strip()
            if self.recipe_name:
                return await self.async_step_recipe()
            errors["name"] = "name_required"
        return self.async_show_form(
            step_id="preset",
            data_schema=vol.Schema(
                {vol.Required("name", default=self.recipe_name): selector.TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_recipe(self, user_input=None):
        if user_input is not None:
            return await getattr(self, f"async_step_{user_input['next_step_id']}")()
        return self.async_show_menu(
            step_id="recipe",
            menu_options=["target", "edit_target", "remove_target", "preset_preview"],
        )

    async def async_step_edit_target(self, user_input=None):
        if user_input is not None:
            self.edit_index = int(user_input["row"])
            return await self.async_step_target()
        return self.async_show_form(
            step_id="edit_target",
            data_schema=vol.Schema(
                {
                    vol.Required("row"): select(
                        [
                            {"value": str(i), "label": self.target_label(t)}
                            for i, t in enumerate(self.rows)
                        ]
                    )
                }
            ),
        )

    async def async_step_remove_target(self, user_input=None):
        if user_input is not None:
            self.rows.pop(int(user_input["row"]))
            return await self.async_step_recipe()
        return self.async_show_form(
            step_id="remove_target",
            data_schema=vol.Schema(
                {
                    vol.Required("row"): select(
                        [
                            {"value": str(i), "label": self.target_label(t)}
                            for i, t in enumerate(self.rows)
                        ]
                    )
                }
            ),
        )

    def target_label(self, target):
        tree = self.group_draft.tree if self.repairing else self.manager.snapshot.tree
        if target.kind == "group":
            group = tree.groups.get(target.id)
            return f"Group: {group.name if group else 'Removed group'} [{target.id[-6:]}]"
        leaf = tree.leaves.get(target.id)
        route = resolve_identity(self.hass, leaf.identity) if leaf else None
        return f"Light: {route or 'Missing light'} [{target.id[-6:]}]"

    def row_defaults(self):
        if self.edit_index is None:
            return {}
        row = self.rows[self.edit_index]
        props = dict(row.properties)
        defaults = {
            "target": f"{row.kind}:{row.id}",
            "power": props.get("power", "inherit"),
            "assign_brightness": "brightness_pct" in props,
            "brightness_pct": props.get("brightness_pct", 100),
            "assign_ct": "color_temp_kelvin" in props,
            "color_temp_kelvin": props.get("color_temp_kelvin", 2700),
            "assign_color": "color" in props,
            "assign_effect": "effect" in props,
            "effect": props.get("effect", ""),
        }
        if color := props.get("color"):
            defaults["color_kind"] = color.kind
            defaults[{"hs": "hs", "rgb": "rgb", "xy": "xy"}[color.kind]] = list(color.value)
        return defaults

    async def async_step_target(self, user_input=None):
        tree = self.group_draft.tree if self.repairing else self.manager.snapshot.tree
        groups, leaves = self.choices(tree)
        options = [
            {"value": f"group:{g['value']}", "label": f"Group: {g['label']}"} for g in groups
        ]
        options += [
            {"value": f"leaf:{leaf['value']}", "label": f"Light: {leaf['label']}"}
            for leaf in leaves
        ]
        defaults = self.row_defaults()
        errors, text = {}, ""
        if user_input is not None:
            try:
                kind, id_ = user_input["target"].split(":", 1)
                values = {}
                if user_input["power"] != "inherit":
                    values["power"] = user_input["power"]
                if user_input["assign_brightness"]:
                    values["brightness_pct"] = user_input["brightness_pct"]
                if user_input["assign_ct"]:
                    values["color_temp_kelvin"] = int(user_input["color_temp_kelvin"])
                if user_input["assign_color"]:
                    mode = user_input["color_kind"]
                    if mode == "hs":
                        value = [user_input["hue"], user_input["saturation"]]
                    elif mode == "xy":
                        value = [user_input["x"], user_input["y"]]
                    else:
                        value = user_input["rgb"]
                    values["color"] = {"kind": mode, "value": value}
                if user_input["assign_effect"]:
                    values["effect"] = user_input["effect"]
                row = Target(kind, id_, values)
                if self.edit_index is None:
                    self.rows.append(row)
                else:
                    self.rows[self.edit_index] = row
                self.edit_index = None
                return await self.async_step_recipe()
            except (ModelError, TypeError, ValueError) as err:
                errors, text = {"base": "invalid_configuration"}, str(err)
            defaults = user_input
        hs = defaults.get("hs", [defaults.get("hue", 0), defaults.get("saturation", 0)])
        xy = defaults.get("xy", [defaults.get("x", 0.33), defaults.get("y", 0.33)])
        effects = sorted(
            {e for o in self.manager.observations().values() for e in o.capabilities.effects}
            | {defaults.get("effect", "")}
        )
        schema = vol.Schema(
            {
                vol.Required(
                    "target", default=defaults.get("target", options[0]["value"] if options else "")
                ): select(options),
                vol.Required("power", default=defaults.get("power", "inherit")): select(
                    [
                        {"value": "inherit", "label": "Inherit; otherwise unchanged"},
                        {"value": "on", "label": "Turn ON"},
                        {"value": "off", "label": "Turn OFF"},
                    ]
                ),
                vol.Required(
                    "assign_brightness", default=defaults.get("assign_brightness", False)
                ): selector.BooleanSelector(),
                vol.Required("brightness_pct", default=defaults.get("brightness_pct", 100)): number(
                    0.01, 100, 0.01
                ),
                vol.Required(
                    "assign_ct", default=defaults.get("assign_ct", False)
                ): selector.BooleanSelector(),
                vol.Required(
                    "color_temp_kelvin", default=defaults.get("color_temp_kelvin", 2700)
                ): number(1, 100000),
                vol.Required(
                    "assign_color", default=defaults.get("assign_color", False)
                ): selector.BooleanSelector(),
                vol.Required("color_kind", default=defaults.get("color_kind", "rgb")): select(
                    ["rgb", "hs", "xy"]
                ),
                vol.Required(
                    "rgb", default=defaults.get("rgb", [255, 255, 255])
                ): selector.ColorRGBSelector(),
                vol.Required("hue", default=hs[0]): number(0, 360, 0.1),
                vol.Required("saturation", default=hs[1]): number(0, 100, 0.1),
                vol.Required("x", default=xy[0]): number(0, 1, 0.001),
                vol.Required("y", default=xy[1]): number(0.001, 1, 0.001),
                vol.Required(
                    "assign_effect", default=defaults.get("assign_effect", False)
                ): selector.BooleanSelector(),
                vol.Required("effect", default=defaults.get("effect", "")): select(
                    [{"value": "", "label": "No local effect assignment"}]
                    + [{"value": e, "label": e} for e in effects if e]
                ),
            }
        )
        return self.async_show_form(
            step_id="target",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "error": text,
                "inheritance": (
                    "Omitted local fields inherit; otherwise leave unchanged. "
                    "Preview shows each source."
                ),
            },
        )

    async def async_step_preset_preview(self, user_input=None):
        errors, summary = {}, ""
        try:
            preset = Preset(self.recipe_id, self.recipe_name, tuple(self.rows))
            base = self.group_draft if self.repairing else self.manager.snapshot
            candidate = replace(
                base,
                presets=dict(self.draft_presets) | {preset.id: preset},
                config_revision=self.revision + 1,
            )
            candidate.validate()
            summary = self.preview(candidate)
        except ModelError as err:
            errors, summary = {"base": "invalid_preset"}, str(err)
        if user_input is not None:
            if user_input["action"] == "edit":
                return await self.async_step_recipe()
            if not errors:
                if self.repairing:
                    self.draft_presets[preset.id] = preset
                    self.repairing = False
                    return await self.async_step_group_preview()
                try:
                    if hasattr(self, "config_entry"):
                        await self.manager.commit(candidate, self.hosts(candidate), self.revision)
                        return self.async_create_entry(title="", data={})
                    await self.manager.prepare_creation(candidate, self.token, self.revision)
                    return self.async_create_entry(
                        title=preset.name, data={"kind": "preset", "creation_token": self.token}
                    )
                except (ModelError, OSError) as err:
                    summary, errors = str(err), {"base": "configuration_changed"}
        return self.async_show_form(
            step_id="preset_preview",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): select(
                        [
                            {"value": "save", "label": "Save"},
                            {"value": "edit", "label": "Edit recipe"},
                        ]
                    )
                }
            ),
            errors=errors,
            description_placeholders={"preview": summary},
        )


class JaiConfigFlow(Editor, ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return JaiOptionsFlow()

    async def async_step_user(self, user_input=None):
        await self.initialize()
        if user_input and "name" in user_input:
            return await self.async_step_group(user_input)
        if user_input:
            return await getattr(self, f"async_step_{user_input['next_step_id']}")()
        return self.async_show_menu(step_id="user", menu_options=["group", "preset"])


class JaiOptionsFlow(Editor, OptionsFlow):
    async def async_step_init(self, user_input=None):
        await self.initialize()
        if self.config_entry.version == 1:
            return await self.async_step_migration(user_input)
        if self.config_entry.data["kind"] == "preset":
            preset = self.manager.snapshot.presets[self.config_entry.entry_id]
            self.recipe_id, self.recipe_name, self.rows = (
                preset.id,
                preset.name,
                list(preset.targets),
            )
            return await self.async_step_preset(user_input)
        self.group_id = self.config_entry.entry_id
        tree = self.manager.snapshot.tree
        group = tree.groups[self.group_id]
        ids = tree.members.get(self.group_id, ())
        routes = self.manager.routes()
        self.group_data = {
            "name": group.name,
            "parent": group.parent or "",
            "children": list(tree.children.get(self.group_id, ())),
            "members": [routes[id_] for id_ in ids if routes[id_]],
            "missing_members": [id_ for id_ in ids if routes[id_] is None],
            "power_policy": group.policy.value,
            "order": group.order,
        }
        return await self.async_step_group(user_input)

    async def async_step_migration(self, user_input=None):
        errors = {}
        try:
            legacy_ids = [
                entry.entry_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.version == 1
            ]
            await self.manager.migrate(user_input)
            for id_ in legacy_ids:
                self.hass.async_create_task(self.retry_migrated_host(id_))
            return self.async_create_entry(title="", data={})
        except OwnershipRequired as err:
            self.ownership = err
        except ModelError as err:
            return self.async_abort(
                reason="migration_failed", description_placeholders={"error": str(err)}
            )
        options = {
            vol.Required(key): select(
                [
                    {"value": id_, "label": self.hass.config_entries.async_get_entry(id_).title}
                    for id_ in ids
                ]
            )
            for key, ids in self.ownership.overlaps.items()
        }
        if user_input:
            errors["base"] = "ownership_required"
        return self.async_show_form(
            step_id="migration",
            data_schema=vol.Schema(options),
            errors=errors,
            description_placeholders={"preview": str(self.ownership.memories)},
        )

    async def retry_migrated_host(self, id_):
        entry = self.hass.config_entries.async_get_entry(id_)
        if not entry or entry.disabled_by:
            return
        if entry.state == ConfigEntryState.MIGRATION_ERROR:
            # Core provides this public API specifically for guided migration
            # repair. Ordinary reload cannot unload a nonrecoverable migration error.
            await self.hass.config_entries.async_retry_migration(id_)
        elif entry.state == ConfigEntryState.NOT_LOADED:
            await self.hass.config_entries.async_setup(id_)
        else:
            await self.hass.config_entries.async_reload(id_)
