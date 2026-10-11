import {
  escapeHtml as e,
  siblingIds,
  treeRows,
  filterLights,
  reorderOperation,
  dropOperations,
} from "./model.js";

class JaiLightingManager extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.tab = "Hierarchy";
    this.expanded = new Set();
    this.selection = new Set();
    this.lightSelection = new Set();
    this.filters = {};
    this.search = "";
    this.busy = false;
    this.shadowRoot.addEventListener("click", (event) => this.click(event));
    this.shadowRoot.addEventListener("change", (event) => this.change(event));
    this.shadowRoot.addEventListener("input", (event) => this.input(event));
    this.shadowRoot.addEventListener("dragstart", (event) =>
      this.dragStart(event),
    );
    this.shadowRoot.addEventListener("dragover", (event) => {
      if (event.target.closest("[data-drop]")) event.preventDefault();
    });
    this.shadowRoot.addEventListener("drop", (event) => this.drop(event));
    this.shadowRoot.addEventListener("pointerdown", (event) =>
      this.pointerStart(event),
    );
    this.shadowRoot.addEventListener("pointermove", (event) =>
      this.pointerMove(event),
    );
    this.shadowRoot.addEventListener("pointerup", (event) =>
      this.pointerEnd(event),
    );
    this.shadowRoot.addEventListener("pointercancel", () => {
      this.pointerDrag = null;
    });
    this.shadowRoot.addEventListener("submit", (event) => {
      event.preventDefault();
      if (this.dialog && event.target.reportValidity())
        this.run(() => this.dialog.submit());
    });
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this.data && !this.loading) this.load();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    if (this.hass) this.load();
    else this.render();
  }
  disconnectedCallback() {
    this.unsubscribe?.();
    this.unsubscribe = null;
    clearTimeout(this.refreshTimer);
  }
  async api(command, values = {}) {
    return this.hass.callWS({
      type: "jai_lighting/manager",
      command,
      ...values,
    });
  }
  async load() {
    if (this.loading) return;
    this.loading = true;
    try {
      const data = await this.api("get");
      if (!this.data)
        Object.keys(data.groups).forEach((id) => this.expanded.add(id));
      this.data = data;
      if (!this.unsubscribe && this.hass.connection?.subscribeEvents) {
        const off = await this.hass.connection.subscribeEvents(() => {
          clearTimeout(this.refreshTimer);
          this.refreshTimer = setTimeout(() => {
            if (!this.busy && !this.dialog) this.load();
          }, 300);
        }, "jai_lighting_manager_updated");
        if (this.isConnected) this.unsubscribe = off;
        else off();
      }
      this.render();
    } catch (error) {
      this.error = error.message || String(error);
      this.render();
    } finally {
      this.loading = false;
    }
  }
  async run(task) {
    if (this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      await task();
    } catch (error) {
      this.error = error.message || String(error);
    } finally {
      this.busy = false;
      this.render();
    }
  }
  items() {
    return [...this.selection]
      .filter((key) => /^(group|leaf):/.test(key))
      .map((key) => {
        const [kind, id] = key.split(":");
        return { kind, id };
      });
  }
  selected(kind) {
    return [...this.selection]
      .filter((key) => key.startsWith(`${kind}:`))
      .map((key) => key.split(":")[1]);
  }
  groupOptions(selected = "") {
    return (
      `<option value="">Root / unassigned</option>` +
      Object.entries(this.data.groups)
        .map(
          ([id]) =>
            `<option value="${e(id)}" ${selected === id ? "selected" : ""}>${e(this.groupPath(id))} [${e(id.slice(-6))}]</option>`,
        )
        .join("")
    );
  }
  name(kind, id) {
    if (kind === "group") return this.data.groups[id]?.name || "Removed group";
    if (kind === "collection")
      return this.data.collections[id]?.name || "Removed collection";
    if (kind === "preset")
      return this.data.presets[id]?.name || "Removed preset";
    const route = this.data.routes[id];
    return (
      this.data.inventory.lights.find((light) => light.entity_id === route)
        ?.name ||
      route ||
      "Missing configured light"
    );
  }
  groupPath(id) {
    const path = [];
    while (id) {
      path.push(this.data.groups[id].name);
      id = this.data.groups[id].parent_group_id;
    }
    return path.reverse().join(" / ");
  }
  open(title, body, submit) {
    this.shadowRoot.querySelector("dialog")?.remove();
    this.dialog = { title, body, submit };
    this.render();
    const modal = this.shadowRoot.querySelector("dialog");
    modal.showModal();
    modal.addEventListener("cancel", (event) => {
      event.preventDefault();
      this.close();
    });
    modal.addEventListener("close", () => {
      if (this.dialog) this.close();
    });
    modal.querySelector("input,select,button")?.focus();
  }
  async close() {
    const flowId = this.dialog?.flowId;
    this.dialog = null;
    if (flowId) {
      try {
        await this.api("cancel_creation", { flow_id: flowId });
      } catch (_) {
        /* Core may already have expired it. */
      }
    }
    this.render();
    this.load();
  }
  presetSummary(before, after, snapshot) {
    const changed = Object.keys(after).filter(
      (id) => JSON.stringify(before[id]) !== JSON.stringify(after[id]),
    );
    if (!changed.length) return "<p>No preset effective values change.</p>";
    return changed
      .map(
        (id) =>
          `<h4>${e(snapshot.presets[id]?.name || this.name("preset", id))}</h4>` +
          Object.entries(after[id])
            .map(
              ([leaf, record]) =>
                `<p><b>${e(this.name("leaf", leaf))}</b><br>${Object.entries(
                  record.properties,
                )
                  .map(
                    ([key, value]) =>
                      `${e(key)}: ${e(typeof value === "object" ? JSON.stringify(value) : value)} <small>from ${e(this.name(record.sources[key].kind, record.sources[key].id))}</small>`,
                  )
                  .join("<br>")}</p>`,
            )
            .join(""),
      )
      .join("");
  }
  async draft(operations, label) {
    const revision = this.data.config_revision;
    const preview = await this.api("preview", { revision, operations });
    this.open(
      "Review changes",
      `<p>${e(label)}</p><p>Configuration editing sends no lighting commands.</p>${this.presetSummary(preview.presets_before, preview.presets_after, preview.snapshot)}`,
      async () => {
        await this.api("apply", { revision, operations });
        this.dialog = null;
        this.selection.clear();
        await this.load();
      },
    );
  }
  async create(kind, data) {
    const response = await this.api("create", {
      revision: this.data.config_revision,
      kind,
      data,
    });
    this.open(
      "Review new " + kind,
      `<p><b>${e(data.name)}</b>${kind === "group" ? ` · Parent: ${e(data.parent ? this.groupPath(data.parent) : "No parent")}` : ""}</p><pre>${e(response.preview)}</pre><p>Saving sends no lighting commands.</p>`,
      async () => {
        await this.api("save_creation", { flow_id: response.flow_id });
        this.dialog = null;
        await this.load();
      },
    );
    this.dialog.flowId = response.flow_id;
  }
  click(event) {
    const button = event.target.closest("[data-action]");
    if (!button || this.busy) return;
    const action = button.dataset.action;
    if (action === "menu") {
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
      return;
    }
    if (action === "tab") {
      this.tab = button.dataset.tab;
      this.selection.clear();
      this.render();
      return;
    }
    if (action === "expand") {
      const id = button.dataset.id;
      this.expanded.has(id) ? this.expanded.delete(id) : this.expanded.add(id);
      this.render();
      return;
    }
    if (action === "expand-all") {
      Object.keys(this.data.groups).forEach((id) => this.expanded.add(id));
      this.render();
      return;
    }
    if (action === "collapse-all") {
      this.expanded.clear();
      this.render();
      return;
    }
    if (action === "cancel") {
      this.close();
      return;
    }
    if (action === "confirm") {
      if (this.shadowRoot.querySelector("dialog form").reportValidity())
        this.run(() => this.dialog.submit());
      return;
    }
    if (action === "refresh") {
      this.load();
      return;
    }
    if (action === "select-lights") {
      filterLights(this.data.inventory, this.filters).forEach((light) =>
        this.lightSelection.add(light.entity_id),
      );
      this.render();
      return;
    }
    if (action === "clear-selection") {
      this.selection.clear();
      this.lightSelection.clear();
      this.render();
      return;
    }
    if (action === "add-row" || action === "remove-row") {
      const rows = this.readRows();
      if (action === "add-row")
        rows.push({
          kind: "group",
          id: Object.keys(this.data.groups)[0] || "",
          properties: { power: "on" },
        });
      else rows.splice(Number(button.dataset.index), 1);
      this.presetDialog(
        this.dialog.editId,
        this.shadowRoot.querySelector('[name="preset-name"]').value,
        rows,
      );
      return;
    }
    this.run(async () => {
      const groups = this.selected("group"),
        leaves = this.selected("leaf"),
        collections = this.selected("collection"),
        presets = this.selected("preset");
      if (action === "new-group") {
        this.open(
          "Create group",
          `<label>Group name<input name="name" required></label><label>Parent<select name="parent" aria-label="Parent">${this.groupOptions(groups[0])}</select></label><label>Bare ON policy<select name="policy" aria-label="Bare ON policy"><option>RESTORE</option><option>FULL</option></select></label>`,
          () =>
            this.create("group", {
              name: this.field("name"),
              parent: this.field("parent"),
              members: [],
              children: [],
              power_policy: this.field("policy"),
              order: 0,
              allow_moves: false,
            }),
        );
      } else if (action === "rename") {
        if (this.selection.size !== 1)
          throw Error("Select exactly one group, collection or preset.");
        const kind = groups.length
            ? "group"
            : collections.length
              ? "collection"
              : "preset",
          id = (
            groups.length ? groups : collections.length ? collections : presets
          )[0];
        if (!id) throw Error("Select one group, collection or preset.");
        this.open(
          "Edit " + kind,
          `<label>Name<input name="name" required value="${e(this.name(kind, id))}"></label>` +
            (kind === "group"
              ? `<label>Bare ON policy<select name="policy" aria-label="Bare ON policy"><option ${this.data.groups[id].power_policy === "RESTORE" ? "selected" : ""}>RESTORE</option><option ${this.data.groups[id].power_policy === "FULL" ? "selected" : ""}>FULL</option></select></label>`
              : ""),
          () =>
            this.draft(
              [
                {
                  action: `update_${kind}`,
                  id,
                  name: this.field("name"),
                  ...(kind === "group" ? { policy: this.field("policy") } : {}),
                },
              ],
              "Update name/options; existing entity IDs are preserved.",
            ),
        );
      } else if (action === "move") {
        const items = this.items();
        if (!items.length)
          throw Error("Select groups or physical lights to move.");
        this.open(
          "Move selected records",
          `<label>Destination<select name="destination" aria-label="Destination">${this.groupOptions()}</select></label><p>A group keeps its whole subtree. Root puts physical lights in Unassigned.</p>`,
          () =>
            this.draft(
              [
                {
                  action: "move",
                  items,
                  parent: this.field("destination") || null,
                },
              ],
              `Move ${items.length} selected records.`,
            ),
        );
      } else if (action === "add-lights") {
        if (groups.length !== 1 || !this.lightSelection.size)
          throw Error(
            "Select a destination group and lights from the browser.",
          );
        const parent = groups[0],
          entity_ids = [...this.lightSelection];
        this.open(
          "Add physical lights",
          `<p>Add ${entity_ids.length} lights to ${e(this.name("group", parent))}.</p><label class="check"><input type="checkbox" name="moves">Confirm moving lights already owned by another group</label>`,
          () =>
            this.draft(
              [
                {
                  action: "add_lights",
                  parent,
                  entity_ids,
                  confirm_moves: this.checked("moves"),
                },
              ],
              "Add the explicitly selected lights.",
            ),
        );
      } else if (action === "remove-lights") {
        if (!leaves.length) throw Error("Select physical lights in the tree.");
        await this.draft(
          [{ action: "remove_lights", ids: leaves }],
          "Remove primary membership; collection memberships and presets are retained.",
        );
      } else if (action === "up" || action === "down") {
        const items = this.items();
        if (items.length !== 1) throw Error("Select one record to reorder.");
        const op = reorderOperation(
          this.data,
          items[0].kind,
          items[0].id,
          action === "up" ? -1 : 1,
        );
        if (op) await this.draft([op], "Reorder within the same parent.");
      } else if (action === "delete") {
        const kind = groups.length
            ? "group"
            : collections.length
              ? "collection"
              : "preset",
          ids =
            kind === "group"
              ? groups
              : kind === "collection"
                ? collections
                : presets;
        if (ids.length !== 1 || this.selection.size !== 1)
          throw Error("Select exactly one record to delete.");
        const id = ids[0];
        this.open(
          "Delete " + kind,
          `<p>Delete ${e(this.name(kind, id))}? This cannot be undone from this panel.</p>` +
            (kind === "group"
              ? `<label>Move its direct lights and children to<select name="destination" aria-label="Destination">${this.groupOptions(this.data.groups[id].parent_group_id)}</select></label><p>Root keeps children as roots and lights as Unassigned. Presets referencing the deleted group must first be repaired.</p>`
              : "<p>Presets referencing a collection must be repaired first. Primary memberships are preserved.</p>") +
            `<label class="check"><input type="checkbox" name="delete-confirm">I confirm deletion</label>`,
          () => {
            if (!this.checked("delete-confirm"))
              throw Error("Confirm deletion first.");
            return this.draft(
              [
                {
                  action: `delete_${kind}`,
                  id,
                  confirm: true,
                  ...(kind === "group"
                    ? { destination: this.field("destination") || null }
                    : {}),
                },
              ],
              `Delete ${this.name(kind, id)} after safely moving its contents.`,
            );
          },
        );
      } else if (action === "new-collection" || action === "edit-collection") {
        const id = action === "edit-collection" ? collections[0] : null;
        if (action === "edit-collection" && collections.length !== 1)
          throw Error("Select one collection.");
        const members = id ? this.data.collections[id].members : [];
        this.open(
          id ? "Edit collection members" : "Create collection",
          `<label>Name<input name="name" required value="${e(id ? this.name("collection", id) : "")}"></label><p>Explicit membership. Labels do not become rules. Missing members are retained unless unchecked.</p><div class="member-list">${Object.keys(
            this.data.leaves,
          )
            .map(
              (leaf) =>
                `<label class="check"><input name="member" type="checkbox" value="${e(leaf)}" ${members.includes(leaf) ? "checked" : ""}>${e(this.name("leaf", leaf))}</label>`,
            )
            .join(
              "",
            )}</div><label class="check"><input name="include-browser" type="checkbox" ${this.lightSelection.size ? "checked" : ""}>Also add ${this.lightSelection.size} lights selected in the browser</label>`,
          () => {
            const chosen = [
              ...this.shadowRoot.querySelectorAll('[name="member"]:checked'),
            ].map((input) => input.value);
            const op = {
              action: "update_collection",
              ...(id ? { id } : {}),
              name: this.field("name"),
              members: chosen,
            };
            const added = this.checked("include-browser")
              ? [...this.lightSelection]
              : [];
            if (added.length) op.add_entity_ids = added;
            return this.draft(
              [op],
              "Save explicit collection membership without changing primary parents.",
            );
          },
        );
      } else if (action === "new-preset" || action === "edit-preset") {
        const id = action === "edit-preset" ? presets[0] : null;
        if (action === "edit-preset" && presets.length !== 1)
          throw Error("Select one preset.");
        const preset = id ? this.data.presets[id] : null;
        this.presetDialog(
          id,
          preset?.name || "",
          preset?.targets.map((row) => ({
            ...row.target,
            properties: row.properties,
          })) || [],
        );
      } else if (action === "activate") {
        if (presets.length !== 1) throw Error("Select one preset to activate.");
        const result = await this.api("activate", { preset_id: presets[0] });
        this.outcome(result);
        await this.load();
      } else if (action.startsWith("collection-")) {
        if (!collections.length) throw Error("Select at least one collection.");
        const properties =
          action === "collection-brightness"
            ? {
                brightness_pct: Number(
                  this.shadowRoot.querySelector(
                    '[name="collection-brightness"]',
                  ).value,
                ),
              }
            : { power: action === "collection-on" ? "on" : "off" };
        this.outcome(await this.api("control", { collections, properties }));
        await this.load();
      } else if (action === "group-on" || action === "group-off") {
        const entity = this.data.entities[button.dataset.id];
        if (!entity)
          throw Error(
            "This group host is missing or disabled; repair its config entry.",
          );
        await this.hass.callService(
          "light",
          action === "group-on" ? "turn_on" : "turn_off",
          { entity_id: entity },
        );
        await this.load();
      }
    });
  }
  outcome(result) {
    this.notice = `${result.status}: ${result.succeeded ?? 0} confirmed, ${Object.keys(result.failed || {}).length} failed. ${Object.keys(result.skipped || {}).length} lights have skipped properties.`;
  }
  field(name) {
    return this.shadowRoot.querySelector(`[name="${name}"]`)?.value ?? "";
  }
  checked(name) {
    return this.shadowRoot.querySelector(`[name="${name}"]`)?.checked ?? false;
  }
  change(event) {
    const target = event.target;
    if (target.dataset.select) {
      target.checked
        ? this.selection.add(target.dataset.select)
        : this.selection.delete(target.dataset.select);
      target
        .closest("[role=treeitem]")
        ?.setAttribute("aria-selected", String(target.checked));
      return;
    }
    if (target.dataset.light) {
      target.checked
        ? this.lightSelection.add(target.dataset.light)
        : this.lightSelection.delete(target.dataset.light);
      this.shadowRoot.querySelector("[data-selection-count]").textContent =
        `${this.lightSelection.size} selected`;
      return;
    }
    if (target.dataset.filter) {
      this.filters[target.dataset.filter] = target.value;
      this.render();
      return;
    }
    if (target.dataset.master)
      this.run(async () => {
        const entity = this.data.entities[target.dataset.master];
        if (!entity) throw Error("Group host is missing.");
        await this.hass.callService("jai_lighting", "set_master", {
          entity_id: entity,
          brightness_pct: Number(target.value),
        });
        await this.load();
      });
  }
  input(event) {
    const key = event.target.dataset.search;
    if (!key) return;
    const value = event.target.value,
      pos = event.target.selectionStart;
    if (key === "tree") this.search = value;
    else this.filters.query = value;
    this.render();
    const input = this.shadowRoot.querySelector(`[data-search="${key}"]`);
    input.focus();
    input.setSelectionRange(pos, pos);
  }
  dragStart(event) {
    const handle = event.target.closest("[data-drag]");
    if (!handle) return;
    const key = handle.dataset.drag;
    if (!this.selection.has(key)) {
      this.selection.clear();
      this.selection.add(key);
    }
    event.dataTransfer.setData(
      "application/jai-lighting",
      JSON.stringify(this.items()),
    );
    event.dataTransfer.effectAllowed = "move";
  }
  async drop(event) {
    const target = event.target.closest("[data-drop]");
    if (!target) return;
    event.preventDefault();
    let items;
    try {
      items = JSON.parse(
        event.dataTransfer.getData("application/jai-lighting"),
      );
    } catch (_) {
      return;
    }
    this.dropItems(items, target, event.clientY);
  }
  dropItems(items, target, y) {
    const [kind, id] = target.dataset.drop.split(":");
    const before = id && y < target.getBoundingClientRect().top + 16;
    this.run(() =>
      this.draft(
        dropOperations(this.data, items, { kind, id }, before),
        "Move/reorder selected records. A group keeps its subtree.",
      ),
    );
  }
  pointerStart(event) {
    if (event.pointerType === "mouse" || !event.target.dataset.drag) return;
    const key = event.target.dataset.drag;
    if (!this.selection.has(key)) {
      this.selection.clear();
      this.selection.add(key);
    }
    this.pointerDrag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      items: this.items(),
      active: false,
    };
    event.target.setPointerCapture(event.pointerId);
  }
  pointerMove(event) {
    if (!this.pointerDrag || this.pointerDrag.id !== event.pointerId) return;
    if (
      Math.hypot(
        event.clientX - this.pointerDrag.x,
        event.clientY - this.pointerDrag.y,
      ) > 12
    )
      this.pointerDrag.active = true;
    if (this.pointerDrag.active) event.preventDefault();
  }
  pointerEnd(event) {
    const drag = this.pointerDrag;
    this.pointerDrag = null;
    if (!drag?.active) return;
    const target = this.shadowRoot
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest("[data-drop]");
    if (target) this.dropItems(drag.items, target, event.clientY);
  }
  targetOptions(value) {
    return [
      ["group", this.data.groups],
      ["collection", this.data.collections],
      ["leaf", this.data.leaves],
    ]
      .flatMap(([kind, records]) =>
        Object.keys(records).map(
          (id) =>
            `<option value="${kind}:${e(id)}" ${value === `${kind}:${id}` ? "selected" : ""}>${e(kind)}: ${e(kind === "group" ? this.groupPath(id) : this.name(kind, id))} [${e(id.slice(-6))}]</option>`,
        ),
      )
      .join("");
  }
  presetDialog(id, name, rows) {
    this.open(
      id ? "Edit preset" : "Create preset",
      `<label>Name<input name="preset-name" required value="${e(name)}"></label><p>Sparse absolute assignments. Hierarchy → collection → physical override. Conflicting overlapping collections are rejected. Omitted fields inherit or stay unchanged.</p>${rows.map((row, index) => this.recipeRow(row, index)).join("")}<button data-action="add-row" type="button">Add assignment</button>`,
      () => {
        const targets = this.readRows(),
          name = this.field("preset-name");
        return id
          ? this.draft(
              [{ action: "update_preset", id, name, targets }],
              "Update the recipe; memberships and group masters are preserved.",
            )
          : this.create("preset", { name, targets });
      },
    );
    this.dialog.editId = id;
  }
  recipeRow(row, index) {
    const p = row.properties,
      color = p.color,
      rgb = color?.kind === "rgb" ? color.value : [255, 255, 255];
    const hex =
      "#" +
      rgb
        .map((value) => Math.round(value).toString(16).padStart(2, "0"))
        .join("");
    const check = (property, label, input) =>
      `<label class="check"><input type="checkbox" data-property="${property}" ${property in p ? "checked" : ""}>${label}</label>${input}`;
    return `<fieldset class="recipe" data-row="${index}"><legend>Assignment ${index + 1}</legend><label>Target<select data-value="target" aria-label="Target">${this.targetOptions(`${row.kind}:${row.id}`)}</select></label><label>Power<select data-value="power" aria-label="Power">${["inherit", "on", "off"].map((value) => `<option ${p.power === value || (!p.power && value === "inherit") ? "selected" : ""}>${value}</option>`).join("")}</select></label>${check("brightness_pct", "Assign brightness (%)", `<input aria-label="Brightness (%)" type="number" min="0.01" max="100" step="0.01" data-value="brightness_pct" value="${p.brightness_pct ?? 100}">`)}${check("color_temp_kelvin", "Assign color temperature (K)", `<input aria-label="Color temperature (K)" type="number" min="1" data-value="color_temp_kelvin" value="${p.color_temp_kelvin ?? 2700}">`)}${check("color", "Assign color", `<select aria-label="Color format" data-value="color-kind">${["rgb", "hs", "xy"].map((kind) => `<option ${color?.kind === kind ? "selected" : ""}>${kind}</option>`).join("")}</select><input aria-label="RGB color" type="color" data-value="rgb" value="${hex}"><label>HS hue / XY x<input type="number" step="any" data-value="color-a" value="${color?.kind !== "rgb" && color ? color.value[0] : 0}"></label><label>HS saturation / XY y<input type="number" step="any" data-value="color-b" value="${color?.kind !== "rgb" && color ? color.value[1] : 0.33}"></label>`)}${check("effect", "Assign effect", `<input aria-label="Effect name" data-value="effect" value="${e(p.effect || "")}" placeholder="Exact advertised effect name">`)}<button type="button" data-action="remove-row" data-index="${index}">Remove assignment</button></fieldset>`;
  }
  readRows() {
    return [...this.shadowRoot.querySelectorAll("[data-row]")].map((row) => {
      const val = (key) => row.querySelector(`[data-value="${key}"]`).value;
      const [kind, id] = val("target").split(":");
      const properties = {};
      if (val("power") !== "inherit") properties.power = val("power");
      for (const property of ["brightness_pct", "color_temp_kelvin", "effect"])
        if (row.querySelector(`[data-property="${property}"]`).checked)
          properties[property] =
            property === "effect" ? val(property) : Number(val(property));
      if (row.querySelector('[data-property="color"]').checked) {
        const mode = val("color-kind");
        properties.color = {
          kind: mode,
          value:
            mode === "rgb"
              ? val("rgb")
                  .slice(1)
                  .match(/../g)
                  .map((v) => parseInt(v, 16))
              : [Number(val("color-a")), Number(val("color-b"))],
        };
      }
      return { kind, id, properties };
    });
  }
  tree() {
    const rows = treeRows(this.data, this.expanded, this.search);
    return `<div class="tree" role="tree" aria-label="Lighting hierarchy" aria-multiselectable="true"><div class="root" data-drop="group:">Root — drop groups here; physical lights become Unassigned</div>${rows.map((row) => this.treeRow(row)).join("")}</div><details ${this.search ? "open" : ""}><summary>Unassigned physical lights (${siblingIds(this.data, "leaf", null).length})</summary>${siblingIds(
      this.data,
      "leaf",
      null,
    )
      .map((id) => this.treeRow({ kind: "leaf", id, depth: 0 }))
      .join("")}</details>`;
  }
  treeRow({ kind, id, depth }) {
    const key = `${kind}:${id}`,
      group = kind === "group",
      entity = this.data.entities[id];
    return `<div class="tree-row" role="treeitem" aria-level="${depth + 1}" aria-selected="${this.selection.has(key)}" ${group ? `aria-expanded="${this.expanded.has(id)}"` : ""} data-drop="${e(key)}" style="--depth:${depth}"><button type="button" class="drag" data-drag="${e(key)}" draggable="true" aria-label="Drag ${e(this.name(kind, id))}">⠿</button><label class="node"><input type="checkbox" data-select="${e(key)}" ${this.selection.has(key) ? "checked" : ""}><span>${e(this.name(kind, id))}${!group ? `<small>${e(this.data.routes[id] || "Registry entry missing")}</small>` : !entity ? "<small>Host missing / retained record</small>" : ""}</span></label>${group ? `<button type="button" data-action="expand" data-id="${e(id)}" aria-label="${this.expanded.has(id) ? "Collapse" : "Expand"} ${e(this.name(kind, id))}">${this.expanded.has(id) ? "▾" : "▸"}</button><div class="controls"><button type="button" data-action="group-on" data-id="${e(id)}">ON</button><button type="button" data-action="group-off" data-id="${e(id)}">OFF</button><label>Relative master<input aria-label="${e(this.name(kind, id))} relative master" type="range" min="1" max="100" step="1" data-master="${e(id)}" value="${Math.max(1, Math.round(((this.data.runtime.group_masters[id] ?? 255) * 100) / 255))}"></label></div>` : ""}</div>`;
  }
  browser() {
    const lights = filterLights(this.data.inventory, this.filters);
    const options = (key, items) =>
      `<option value="">All ${key}</option>` +
      items
        .map(
          (item) =>
            `<option value="${e(item.id)}" ${this.filters[key === "labels" ? "label" : key === "areas" ? "area_id" : "device_id"] === item.id ? "selected" : ""}>${e(item.name)}</option>`,
        )
        .join("");
    return `<aside><h3>Physical light browser</h3><input aria-label="Search physical lights" data-search="lights" placeholder="Name or entity ID" value="${e(this.filters.query || "")}"><div class="filters"><label>Area<select data-filter="area_id" aria-label="Area">${options("areas", this.data.inventory.areas)}</select></label><label>HA label<select data-filter="label" aria-label="HA label">${options("labels", this.data.inventory.labels)}</select></label><label>Device<select data-filter="device_id" aria-label="Device">${options("devices", this.data.inventory.devices)}</select></label></div><p>${lights.length} matching lights · <span data-selection-count>${this.lightSelection.size} selected</span></p><button data-action="select-lights">Select all matching</button><p class="hint">Labels are selection helpers. Membership stays explicit.</p><div class="member-list">${lights.map((light) => `<label class="check"><input type="checkbox" data-light="${e(light.entity_id)}" ${this.lightSelection.has(light.entity_id) ? "checked" : ""}><span>${e(light.name)}<small>${e(light.entity_id)} · ${e(light.state)}</small></span></label>`).join("")}</div></aside>`;
  }
  collectionList() {
    return `<div class="toolbar"><button data-action="new-collection">Create collection</button><button data-action="edit-collection">Edit members</button><button data-action="rename">Rename</button><button data-action="delete">Delete</button></div><p>Collections cross branches without changing primary parents. Brightness is absolute and preserves OFF state.</p><div class="toolbar"><button data-action="collection-on">ON selected</button><button data-action="collection-off">OFF selected</button><label>Brightness (%)<input name="collection-brightness" type="number" min="0.01" max="100" value="50"></label><button data-action="collection-brightness">Apply brightness</button></div>${
      Object.entries(this.data.collections)
        .map(
          ([id, c]) =>
            `<article><label class="check"><input type="checkbox" data-select="collection:${e(id)}" ${this.selection.has(`collection:${id}`) ? "checked" : ""}><b>${e(c.name)}</b></label><p>${c.members.map((leaf) => e(this.name("leaf", leaf))).join(" · ") || "Empty collection"}</p></article>`,
        )
        .join("") ||
      "<p>No collections yet. Select lights in the browser, then create one.</p>"
    }`;
  }
  presetList() {
    return `<div class="toolbar"><button data-action="new-preset">Create preset</button><button data-action="edit-preset">Edit recipe</button><button data-action="rename">Rename</button><button data-action="delete">Delete</button><button data-action="activate">Activate selected</button></div>${
      Object.entries(this.data.presets)
        .map(
          ([id, p]) =>
            `<article><label class="check"><input type="checkbox" data-select="preset:${e(id)}" ${this.selection.has(`preset:${id}`) ? "checked" : ""}><b>${e(p.name)}</b></label><p>${p.targets.map((row) => `${e(this.name(row.target.kind, row.target.id))}: ${e(JSON.stringify(row.properties))}`).join("<br>")}</p></article>`,
        )
        .join("") || "<p>No presets yet.</p>"
    }`;
  }
  render() {
    // A dialog retains its own input DOM while a request is running.
    if (this.dialog && this.shadowRoot.querySelector("dialog[open]")) {
      this.shadowRoot.querySelector(".dialog-error").textContent =
        this.error || "";
      this.shadowRoot.querySelector('[data-action="confirm"]').disabled =
        this.busy;
      return;
    }
    const content = !this.data
      ? "<p>Loading J'ai Lighting…</p>"
      : this.tab === "Hierarchy"
        ? `<div class="toolbar"><button data-action="new-group">Create group</button><button data-action="rename">Name / policy</button><button data-action="move">Move to…</button><button data-action="add-lights">Add selected lights</button><button data-action="remove-lights">Remove membership</button><button data-action="up">Move up</button><button data-action="down">Move down</button><button data-action="delete">Delete group</button></div><div class="toolbar"><input aria-label="Search hierarchy" data-search="tree" value="${e(this.search)}" placeholder="Search hierarchy"><button data-action="expand-all">Expand all</button><button data-action="collapse-all">Collapse all</button></div>${this.tree()}`
        : this.tab === "Collections"
          ? this.collectionList()
          : this.tab === "Presets"
            ? this.presetList()
            : `<h3>Settings and safety</h3><p>Configuration revision: ${this.data.config_revision} · Storage schema: ${this.data.schema_version}</p><p>Recovery: ${this.data.recovery_pending ? "Pending — repair required" : "No pending transaction"}</p><p>Primary membership is single-parent. Labels never create dynamic membership. Configuration editing sends no light commands.</p><p>The relative master preserves power. Native HA brightness services retain their existing ON behavior.</p><a href="/config/integrations/integration/jai_lighting">Open integration settings and native editors</a>`;
    this.shadowRoot.innerHTML = `<style>${styles}</style><header><button data-action="menu" aria-label="Open Home Assistant sidebar">☰</button><h1>J'ai Lighting</h1><button data-action="refresh">Refresh</button></header><nav aria-label="Manager sections">${["Hierarchy", "Collections", "Presets", "Settings"].map((tab) => `<button data-action="tab" data-tab="${tab}" aria-current="${tab === this.tab ? "page" : "false"}">${tab}</button>`).join("")}</nav><div class="status" role="status">${e(this.notice || "")}</div><div class="error" role="alert">${e(this.error || "")}</div><div class="workspace"><main>${content}<button data-action="clear-selection">Clear selection</button></main>${this.data && this.tab !== "Settings" ? this.browser() : ""}</div>${this.dialog ? `<dialog aria-label="${e(this.dialog.title)}"><h2>${e(this.dialog.title)}</h2><form>${this.dialog.body}</form><p class="dialog-error error" role="alert">${e(this.error || "")}</p><footer><button data-action="cancel">Cancel</button><button data-action="confirm">Continue / save</button></footer></dialog>` : ""}`;
  }
}

const styles = `
:host{display:block;box-sizing:border-box;min-height:100vh;background:var(--primary-background-color,#f6f7f9);color:var(--primary-text-color,#222);font:14px var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
*{box-sizing:border-box}header{display:flex;align-items:center;gap:12px;padding:12px 20px;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff)}h1{font-size:22px;margin:0;flex:1}nav{display:flex;border-bottom:1px solid var(--divider-color,#ddd);padding:8px 16px;gap:8px;overflow:auto}nav [aria-current=page]{background:var(--primary-color,#039be5);color:var(--text-primary-color,#fff)}
button,input,select{font:inherit}button{cursor:pointer;border:1px solid var(--divider-color,#ccd);border-radius:8px;background:var(--card-background-color,#fff);color:var(--primary-text-color,#222);padding:9px 12px;min-height:44px}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--primary-color,#039be5);outline-offset:2px}button:disabled{opacity:.5;cursor:wait}input:not([type=checkbox]):not([type=range]):not([type=color]),select{width:100%;min-height:44px;padding:8px;border:1px solid var(--divider-color,#ccd);border-radius:6px;background:var(--card-background-color,#fff);color:inherit}input[type=checkbox]{width:22px;height:22px;flex-shrink:0}input[type=range]{width:120px}label{display:block;margin:6px 0}small{display:block;color:var(--secondary-text-color,#667);font-size:12px;overflow-wrap:anywhere}.workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,340px);gap:20px;padding:16px}main,aside{min-width:0}aside,article{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#dde);border-radius:12px;padding:16px}article{margin:10px 0}.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:12px}.toolbar label{max-width:160px}.toolbar>input{max-width:320px}.tree{border:1px solid var(--divider-color,#ddd);border-radius:12px;overflow:hidden;background:var(--card-background-color,#fff)}.tree-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 10px 8px calc(8px + min(var(--depth),8)*18px);border-bottom:1px solid var(--divider-color,#eee)}.tree-row[aria-selected=true]{background:var(--secondary-background-color,#e8eef5)}.node{display:flex;align-items:center;gap:8px;flex:1;min-width:110px;overflow-wrap:anywhere}.drag{touch-action:none;cursor:grab;padding:4px;min-width:30px}.controls{display:flex;gap:6px;align-items:center}.controls label{font-size:11px}.controls button{padding:6px}.root{padding:16px;border:2px dashed var(--divider-color,#cbd);color:var(--secondary-text-color,#667)}.check{display:flex;align-items:center;gap:8px;min-height:44px}.member-list{max-height:48vh;overflow:auto}.filters{display:grid;gap:4px}.hint{color:var(--secondary-text-color,#667);font-size:12px}.status,.error{padding:0 20px}.error{color:var(--error-color,#b00020)}dialog{color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;width:min(700px,94vw);max-height:88vh;overflow:auto;padding:20px}dialog::backdrop{background:#0008}dialog footer{position:sticky;bottom:-20px;background:var(--card-background-color,#fff);display:flex;justify-content:flex-end;gap:10px;padding:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}.recipe{display:grid;grid-template-columns:1fr 1fr;gap:6px;border:1px solid var(--divider-color,#ddd);margin:12px 0;border-radius:8px}.recipe>label:first-of-type{grid-column:1/-1}details{margin-top:14px}summary{cursor:pointer;padding:12px}
@media(max-width:1000px){.controls{width:100%;padding-left:30px}.workspace{grid-template-columns:minmax(0,1fr) 280px}}
@media(max-width:700px){.workspace{display:flex;flex-direction:column;padding:10px}.tree-row{padding-left:calc(4px + min(var(--depth),5)*12px)}header{padding:10px}h1{font-size:19px}.controls{flex-wrap:wrap}.recipe{grid-template-columns:1fr}.recipe>label:first-of-type{grid-column:auto}nav{gap:4px;padding:8px}nav button{padding:8px}.member-list{max-height:45vh}}
`;
if (!customElements.get("jai-lighting-manager"))
  customElements.define("jai-lighting-manager", JaiLightingManager);
