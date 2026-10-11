import {
  escapeHtml as e,
  siblingIds,
  treeRows,
  filterLights,
  reorderOperation,
  planDrop,
  unassignedLights,
  groupStats,
  selectVisible,
  movableItems,
} from "./model.js";
import { styles } from "./styles.js";
import "./group-picker.js";
import { renderKeyed, captureScroll, restoreScroll } from "./dom.js";

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
    this.editQueue = Promise.resolve();
    this.pendingEdits = 0;
    this.listSearch = {};
    this.panelMode = "hierarchy";
    this.contexts = {};
    this.scrollStates = {};
    this.shadowRoot.addEventListener("click", (event) => this.click(event));
    this.shadowRoot.addEventListener("change", (event) => this.change(event));
    this.shadowRoot.addEventListener("input", (event) => this.input(event));
    this.shadowRoot.addEventListener("dragstart", (event) =>
      this.dragStart(event),
    );
    this.shadowRoot.addEventListener("dragover", (event) =>
      this.dragOver(event),
    );
    this.shadowRoot.addEventListener("dragend", () => this.clearDrop());
    this.shadowRoot.addEventListener("keydown", (event) => this.keyDown(event));
    this.shadowRoot.addEventListener("dblclick", (event) => {
      const row = event.target.closest('[data-key^="group:"]');
      if (row && !event.target.closest("button"))
        this.toggleExpanded(row.dataset.key.split(":")[1]);
    });
    this.shadowRoot.addEventListener("contextmenu", (event) => {
      const row = event.target.closest("[data-key]");
      if (!row) return;
      event.preventDefault();
      if (!this.selection.has(row.dataset.key))
        this.selectRow(row.dataset.key, event);
      const menu = this.shadowRoot.querySelector(".context-menu");
      if (menu) menu.open = true;
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
      this.clearDrop();
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
    // HA supplies states reactively, including unassigned lights outside our topology.
    else if (this.data && value.states) {
      let changed = false;
      const lights = this.data.inventory.lights.map((light) => {
        const state = value.states[light.entity_id]?.state || "unavailable";
        if (state === light.state) return light;
        changed = true;
        return { ...light, state };
      });
      if (changed) {
        this.data = {
          ...this.data,
          inventory: { ...this.data.inventory, lights },
        };
        this.render();
      }
    }
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
    cancelAnimationFrame(this.scrollFrame);
    clearTimeout(this.panelHoverTimer);
  }
  async api(command, values = {}) {
    return this.hass.callWS({
      type: "jai_lighting/manager",
      command,
      ...values,
    });
  }
  async load() {
    if (this.loading) return this.loading;
    this.loading = this.fetchData();
    try {
      return await this.loading;
    } finally {
      this.loading = null;
    }
  }
  async fetchData() {
    try {
      const data = await this.api("get");
      if (!this.expansionLoaded) {
        this.expansionLoaded = true;
        this.expansionKey = `jai-lighting:expanded:${this.hass.user?.id || "local"}`;
        try {
          const stored = JSON.parse(localStorage.getItem(this.expansionKey));
          this.expanded = new Set(
            Array.isArray(stored) ? stored : Object.keys(data.groups),
          );
        } catch (_) {
          this.expanded = new Set(Object.keys(data.groups));
        }
      }
      this.expanded = new Set(
        [...this.expanded].filter((id) => data.groups[id]),
      );
      this.reconcileSelections(data);
      this.data = data;
      if (!this.unsubscribe && this.hass.connection?.subscribeEvents) {
        const off = await this.hass.connection.subscribeEvents(() => {
          clearTimeout(this.refreshTimer);
          this.refreshTimer = setTimeout(() => {
            if (!this.busy && !this.dialog) this.load();
            else this.externalRefresh = true;
          }, 300);
        }, "jai_lighting_manager_updated");
        if (this.isConnected) this.unsubscribe = off;
        else off();
      }
      this.render();
      return data;
    } catch (error) {
      this.error = error.message || String(error);
      this.render();
      return null;
    }
  }
  reconcileSelections(data) {
    const validKey = (key) => {
      const [kind, id] = key.split(":");
      if (kind === "leaf") return data.leaves[id]?.parent_group_id != null;
      return (
        kind === "group"
          ? data.groups
          : kind === "leaf"
            ? data.leaves
            : kind === "collection"
              ? data.collections
              : data.presets
      )[id];
    };
    const reconcile = (selection, lights, tab) => {
      const available = new Set(
        unassignedLights(data, {}, tab === "Hierarchy").map(
          (light) => light.entity_id,
        ),
      );
      return {
        selection: new Set([...selection].filter(validKey)),
        lights: new Set([...lights].filter((id) => available.has(id))),
      };
    };
    const current = reconcile(this.selection, this.lightSelection, this.tab);
    this.selection = current.selection;
    this.lightSelection = current.lights;
    for (const [tab, context] of Object.entries(this.contexts))
      Object.assign(context, reconcile(context.selection, context.lights, tab));
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
      this.busy = this.pendingEdits > 0;
      this.render();
      if (this.externalRefresh && !this.dialog && !this.busy) {
        this.externalRefresh = false;
        await this.load();
      }
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
  groupPicker(name, selected = "", blocked = []) {
    return `<jai-group-picker name="${e(name)}" aria-label="${e(name === "parent" ? "Parent" : "Destination")}" value="${e(selected || "")}" data-blocked="${e(JSON.stringify(blocked))}"></jai-group-picker>`;
  }
  configurePickers() {
    this.shadowRoot.querySelectorAll("jai-group-picker").forEach((picker) => {
      const blocked = new Set(JSON.parse(picker.dataset.blocked || "[]"));
      for (const id of Object.keys(this.data.groups)) {
        let parent = this.data.groups[id].parent_group_id;
        while (parent) {
          if (blocked.has(parent)) {
            blocked.add(id);
            break;
          }
          parent = this.data.groups[parent]?.parent_group_id;
        }
      }
      picker.configure({
        groups: this.data.groups,
        blocked,
        allowRoot: !picker.dataset.value,
        prefix: !!picker.dataset.value,
        extras: picker.dataset.value
          ? ["collection", "leaf"].flatMap((kind) =>
              Object.keys(
                kind === "collection"
                  ? this.data.collections
                  : this.data.leaves,
              ).map((id) => ({
                kind,
                value: `${kind}:${id}`,
                name: this.name(kind, id),
              })),
            )
          : [],
      });
    });
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
  persistExpansion() {
    try {
      localStorage.setItem(
        this.expansionKey,
        JSON.stringify([...this.expanded]),
      );
    } catch (_) {
      /* Private browsing can deny storage. */
    }
  }
  toggleExpanded(id) {
    this.expanded.has(id) ? this.expanded.delete(id) : this.expanded.add(id);
    this.persistExpansion();
    this.render();
  }
  visibleKeys() {
    if (this.tab === "Hierarchy")
      return treeRows(this.data, this.expanded, this.search).map(
        (row) => `${row.kind}:${row.id}`,
      );
    const kind = this.tab === "Collections" ? "collection" : "preset";
    return Object.keys(
      this.tab === "Collections" ? this.data.collections : this.data.presets,
    )
      .filter((id) =>
        this.name(kind, id)
          .toLocaleLowerCase()
          .includes((this.listSearch[this.tab] || "").toLocaleLowerCase()),
      )
      .map((id) => `${kind}:${id}`);
  }
  selectRow(key, event) {
    this.selection = selectVisible(
      this.selection,
      this.visibleKeys(),
      key,
      this.anchor,
      {
        toggle: event.metaKey || event.ctrlKey,
        range: event.shiftKey,
      },
    );
    if (!event.shiftKey) this.anchor = key;
    this.focusKey = key;
    this.render();
    this.shadowRoot
      .querySelector(`[data-key="${CSS.escape(key)}"]`)
      ?.focus({ preventScroll: true });
  }
  keyDown(event) {
    if (this.dialog || event.target.matches("input,select,textarea,button"))
      return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      this.undoEdit();
      return;
    }
    const row = event.target.closest("[data-key]");
    if (!row) return;
    const keys = this.visibleKeys(),
      key = row.dataset.key,
      index = keys.indexOf(key);
    let destination;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      this.selection = new Set(keys);
      this.render();
      return;
    }
    if (event.key === "ArrowDown")
      destination = keys[Math.min(keys.length - 1, index + 1)];
    if (event.key === "ArrowUp") destination = keys[Math.max(0, index - 1)];
    if (event.key === "Home") destination = keys[0];
    if (event.key === "End") destination = keys.at(-1);
    const [kind, id] = key.split(":");
    if (event.key === "ArrowRight" && kind === "group") {
      if (!this.expanded.has(id)) {
        this.toggleExpanded(id);
        destination = key;
      } else destination = keys[index + 1] || key;
    }
    if (event.key === "ArrowLeft") {
      if (kind === "group" && this.expanded.has(id)) {
        this.toggleExpanded(id);
        destination = key;
      } else {
        const parent = (kind === "group" ? this.data.groups : this.data.leaves)[
          id
        ]?.parent_group_id;
        destination = parent ? `group:${parent}` : key;
      }
    }
    if (destination) {
      event.preventDefault();
      this.selectRow(destination, { shiftKey: event.shiftKey });
      this.shadowRoot
        .querySelector(`[data-key="${CSS.escape(destination)}"]`)
        ?.scrollIntoView({ block: "nearest" });
    } else if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      this.selectRow(key, { ctrlKey: event.key === " " });
    } else if (event.key === "Escape") {
      this.selection.clear();
      this.render();
    }
  }
  saveTopology(operations, label, undoing = false) {
    if (!operations.length) return Promise.resolve();
    this.pendingEdits++;
    const task = async () => {
      this.busy = true;
      this.error = "";
      this.notice = "Saving…";
      const before = this.data,
        revision = before.config_revision;
      try {
        const preview = await this.api("preview", { revision, operations });
        if (!operations.some((op) => op.action === "add_lights")) {
          this.data = { ...before, ...preview.snapshot };
          this.render();
        }
        const result = await this.api("apply", { revision, operations });
        this.undo =
          !undoing && result.undo_operations?.length
            ? {
                revision: result.revision,
                operations: result.undo_operations,
                label,
              }
            : null;
        this.data = before;
        const current = await this.load();
        this.notice = current
          ? undoing
            ? "Change undone"
            : label
          : "Saved. Refresh to reload the current hierarchy.";
      } catch (error) {
        this.data = before;
        this.error = error.message || String(error);
        this.notice =
          "Change not saved. The current configuration has been restored.";
        this.undo = null;
        await this.load();
      } finally {
        this.pendingEdits--;
        this.busy = this.pendingEdits > 0;
        this.render();
        if (this.externalRefresh && !this.busy && !this.dialog) {
          this.externalRefresh = false;
          await this.load();
        }
      }
    };
    this.editQueue = this.editQueue.then(task, task);
    return this.editQueue;
  }
  async undoEdit() {
    if (!this.undo || this.busy) return;
    if (this.data.config_revision !== this.undo.revision) {
      this.error =
        "Configuration changed in another session. Undo is unavailable; refresh and review the current hierarchy.";
      this.undo = null;
      this.render();
      return;
    }
    const inverse = this.undo.operations;
    this.undo = null;
    await this.saveTopology(inverse, "Change undone", true);
  }
  async ordinary(operations, label) {
    this.dialog = null;
    this.render();
    return this.saveTopology(operations, label);
  }
  click(event) {
    if (this.suppressClick) {
      const { x, y, until } = this.suppressClick;
      this.suppressClick = null;
      if (
        performance.now() < until &&
        Math.hypot(event.clientX - x, event.clientY - y) < 24
      )
        return;
    }
    const button = event.target.closest("[data-action]");
    const row = event.target.closest("[data-key]");
    if (!button) {
      if (row && !event.target.closest("input,select,summary"))
        this.selectRow(row.dataset.key, event);
      else if (event.target.matches("main,.tree,.root,.empty-state")) {
        this.selection.clear();
        this.render();
      }
      return;
    }
    const action = button.dataset.action;
    if (button.disabled) return;
    if (action === "menu") {
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
      return;
    }
    if (action === "tab") {
      this.contexts[this.tab] = {
        selection: this.selection,
        lights: this.lightSelection,
        anchor: this.anchor,
        focus: this.focusKey,
      };
      this.tab = button.dataset.tab;
      const context = this.contexts[this.tab];
      this.selection = context?.selection || new Set();
      this.lightSelection = context?.lights || new Set();
      this.anchor = context?.anchor;
      this.focusKey = context?.focus;
      this.reconcileSelections(this.data);
      this.render();
      return;
    }
    if (action === "panel") {
      this.panelMode = button.dataset.panel;
      this.render();
      return;
    }
    if (action === "expand") {
      this.toggleExpanded(button.dataset.id);
      return;
    }
    if (action === "expand-all" || action === "collapse-all") {
      this.expanded = new Set(
        action === "expand-all" ? Object.keys(this.data.groups) : [],
      );
      this.persistExpansion();
      this.render();
      return;
    }
    if (action === "undo") {
      this.undoEdit();
      return;
    }
    if (action === "remove-lights") {
      const ids = this.selected("leaf").filter(
        (id) => this.data.leaves[id].parent_group_id !== null,
      );
      if (ids.length)
        this.saveTopology(
          [{ action: "remove_lights", ids }],
          `Removed ${ids.length} light${ids.length === 1 ? "" : "s"} from the hierarchy`,
        );
      return;
    }
    if (action === "add-lights") {
      const groups = this.selected("group");
      const available = new Set(
        unassignedLights(this.data).map((light) => light.entity_id),
      );
      const entity_ids = [...this.lightSelection].filter((id) =>
        available.has(id),
      );
      if (groups.length === 1 && this.selection.size === 1 && entity_ids.length)
        this.saveTopology(
          [{ action: "add_lights", parent: groups[0], entity_ids }],
          `Added ${entity_ids.length} lights to ${this.name("group", groups[0])}`,
        );
      return;
    }
    if (action === "up" || action === "down") {
      const items = this.items();
      if (items.length === 1) {
        const op = reorderOperation(
          this.data,
          items[0].kind,
          items[0].id,
          action === "up" ? -1 : 1,
        );
        if (op)
          this.saveTopology(
            [op],
            `Reordered ${this.name(items[0].kind, items[0].id)}`,
          );
      }
      return;
    }
    if (this.busy) return;
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
      unassignedLights(
        this.data,
        this.filters,
        this.tab === "Hierarchy",
      ).forEach((light) => this.lightSelection.add(light.entity_id));
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
          `<label>Group name<input name="name" required></label><label>Parent${this.groupPicker("parent", groups[0] || "")}</label><label>Bare ON policy<select name="policy" aria-label="Bare ON policy"><option>RESTORE</option><option>FULL</option></select></label>`,
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
            (kind === "group"
              ? this.ordinary.bind(this)
              : this.draft.bind(this))(
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
        const items = movableItems(this.data, this.items());
        if (!items.length)
          throw Error("Select groups or physical lights to move.");
        this.open(
          "Move selected records",
          `<label>Destination${this.groupPicker(
            "destination",
            "",
            items
              .filter((item) => item.kind === "group")
              .map((item) => item.id),
          )}</label><p>A group keeps its whole subtree. Root puts physical lights in Unassigned.</p>`,
          () =>
            this.ordinary(
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
              ? `<label>Move its direct lights and children to${this.groupPicker("destination", this.data.groups[id].parent_group_id, [id])}</label><p>Root keeps children as roots and lights as Unassigned. Presets referencing the deleted group must first be repaired.</p>`
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
      this.render();
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
    else if (key === "list") this.listSearch[this.tab] = value;
    else this.filters.query = value;
    this.render();
    const input = this.shadowRoot.querySelector(`[data-search="${key}"]`);
    input.focus();
    input.setSelectionRange(pos, pos);
  }
  dragItems(key, single = false) {
    if (key.startsWith("inventory:")) {
      const id = key.slice(10);
      // Dragging an unselected item (or modifier-drag) retains unrelated selections.
      return (
        single || !this.lightSelection.has(id) ? [id] : [...this.lightSelection]
      ).map((id) => ({ kind: "inventory", id }));
    }
    if (!this.selection.has(key)) {
      this.selection = new Set([key]);
      this.anchor = key;
    }
    return movableItems(this.data, this.items());
  }
  dragStart(event) {
    const handle = event.target.closest("[data-drag]");
    if (!handle || this.busy) {
      event.preventDefault();
      return;
    }
    this.dragging = this.dragItems(
      handle.dataset.drag,
      event.metaKey || event.ctrlKey,
    );
    event.dataTransfer.setData(
      "application/jai-lighting",
      JSON.stringify(this.dragging),
    );
    event.dataTransfer.effectAllowed = "move";
  }
  dropPlan(items, target, y) {
    const [kind, id] = target.dataset.drop.split(":");
    const rect = target.getBoundingClientRect(),
      fraction = (y - rect.top) / rect.height;
    const position = !id
      ? "inside"
      : kind === "leaf"
        ? fraction < 0.5
          ? "before"
          : "after"
        : fraction < 0.22
          ? "before"
          : fraction > 0.78
            ? "after"
            : "inside";
    return {
      operations: planDrop(this.data, items, { kind, id }, position),
      position,
    };
  }
  clearDrop(stopScroll = true) {
    if (stopScroll) {
      cancelAnimationFrame(this.scrollFrame);
      this.scrollFrame = null;
      clearTimeout(this.panelHoverTimer);
      this.hoverPanel = null;
    }
    this.shadowRoot.querySelectorAll("[data-drop-state]").forEach((el) => {
      delete el.dataset.dropState;
      delete el.dataset.dropPosition;
      el.removeAttribute("title");
    });
  }
  highlightDrop(items, target, y) {
    this.clearDrop(false);
    try {
      const plan = this.dropPlan(items, target, y);
      target.dataset.dropState = "valid";
      target.dataset.dropPosition = plan.position;
      return plan;
    } catch (error) {
      target.dataset.dropState = "invalid";
      target.title = error.message;
      return null;
    }
  }
  dragOver(event) {
    if (!this.dragging || this.busy) return;
    this.autoScroll(event.clientX, event.clientY);
    this.dragPanel(event.target);
    const target = event.target.closest("[data-drop]");
    if (!target) {
      this.clearDrop(false);
      return;
    }
    const plan = this.highlightDrop(this.dragging, target, event.clientY);
    event.preventDefault();
    event.dataTransfer.dropEffect = plan ? "move" : "none";
  }
  drop(event) {
    const target = event.target.closest("[data-drop]");
    if (!target || this.busy) return;
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
    this.dragging = null;
  }
  dropItems(items, target, y) {
    try {
      const plan = this.dropPlan(items, target, y);
      this.clearDrop();
      this.saveTopology(
        plan.operations,
        `Moved ${items.length} item${items.length === 1 ? "" : "s"}`,
      );
    } catch (error) {
      this.clearDrop();
      this.error = error.message;
      this.render();
    }
  }
  pointerStart(event) {
    if (event.pointerType === "mouse" || this.busy) return;
    const handle = event.target.closest(".drag[data-drag]");
    if (!handle) return;
    this.pointerDrag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      items: this.dragItems(handle.dataset.drag),
      active: false,
    };
    handle.setPointerCapture(event.pointerId);
  }
  pointerMove(event) {
    const drag = this.pointerDrag;
    if (!drag || drag.id !== event.pointerId) return;
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 12)
      drag.active = true;
    if (drag.active) {
      drag.lastX = event.clientX;
      drag.lastY = event.clientY;
      event.preventDefault();
      const pointed = this.shadowRoot.elementFromPoint(
        event.clientX,
        event.clientY,
      );
      this.dragPanel(pointed);
      const target = pointed?.closest("[data-drop]");
      if (target) this.highlightDrop(drag.items, target, event.clientY);
      else this.clearDrop(false);
      this.autoScroll(event.clientX, event.clientY);
    }
  }
  pointerEnd(event) {
    const drag = this.pointerDrag;
    this.pointerDrag = null;
    if (!drag?.active) return;
    const x = drag.lastX ?? event.clientX,
      y = drag.lastY ?? event.clientY;
    this.suppressClick = { x, y, until: performance.now() + 400 };
    const target = this.shadowRoot
      .elementFromPoint(x, y)
      ?.closest("[data-drop]");
    if (target) this.dropItems(drag.items, target, y);
    else this.clearDrop();
  }
  dragPanel(element) {
    const mode = element?.closest('[data-action="panel"]')?.dataset.panel;
    if (
      !matchMedia("(max-width:760px)").matches ||
      mode === this.panelMode ||
      !mode
    ) {
      clearTimeout(this.panelHoverTimer);
      this.hoverPanel = null;
      return;
    }
    if (this.hoverPanel === mode) return;
    clearTimeout(this.panelHoverTimer);
    this.hoverPanel = mode;
    this.panelHoverTimer = setTimeout(() => {
      if (this.dragging || this.pointerDrag?.active) {
        this.panelMode = mode;
        this.render();
      }
      this.hoverPanel = null;
    }, 350);
  }
  autoScroll(x, y) {
    this.scrollPoint = { x, y };
    if (this.scrollFrame) return;
    const tick = () => {
      this.scrollFrame = null;
      if (!this.dragging && !this.pointerDrag?.active) return;
      const { x, y } = this.scrollPoint;
      const panel = this.shadowRoot
        .elementFromPoint(x, y)
        ?.closest("[data-scroll]");
      if (!panel) return;
      const rect = panel.getBoundingClientRect();
      const direction = y < rect.top + 40 ? -1 : y > rect.bottom - 40 ? 1 : 0;
      if (!direction) return;
      panel.scrollTop += direction * 9;
      this.scrollFrame = requestAnimationFrame(tick);
    };
    this.scrollFrame = requestAnimationFrame(tick);
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
    return `<fieldset class="recipe" data-row="${index}"><legend>Assignment ${index + 1}</legend><label>Target<jai-group-picker data-value="target" aria-label="Target" value="${e(`${row.kind}:${row.id}`)}"></jai-group-picker></label><label>Power<select data-value="power" aria-label="Power">${["inherit", "on", "off"].map((value) => `<option ${p.power === value || (!p.power && value === "inherit") ? "selected" : ""}>${value}</option>`).join("")}</select></label>${check("brightness_pct", "Assign brightness (%)", `<input aria-label="Brightness (%)" type="number" min="0.01" max="100" step="0.01" data-value="brightness_pct" value="${p.brightness_pct ?? 100}">`)}${check("color_temp_kelvin", "Assign color temperature (K)", `<input aria-label="Color temperature (K)" type="number" min="1" data-value="color_temp_kelvin" value="${p.color_temp_kelvin ?? 2700}">`)}${check("color", "Assign color", `<select aria-label="Color format" data-value="color-kind">${["rgb", "hs", "xy"].map((kind) => `<option ${color?.kind === kind ? "selected" : ""}>${kind}</option>`).join("")}</select><input aria-label="RGB color" type="color" data-value="rgb" value="${hex}"><label>HS hue / XY x<input type="number" step="any" data-value="color-a" value="${color?.kind !== "rgb" && color ? color.value[0] : 0}"></label><label>HS saturation / XY y<input type="number" step="any" data-value="color-b" value="${color?.kind !== "rgb" && color ? color.value[1] : 0.33}"></label>`)}${check("effect", "Assign effect", `<input aria-label="Effect name" data-value="effect" value="${e(p.effect || "")}" placeholder="Exact advertised effect name">`)}<button type="button" data-action="remove-row" data-index="${index}">Remove assignment</button></fieldset>`;
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
  button(action, label, enabled = true, reason = "", extra = "") {
    return `<button type="button" data-action="${action}" ${enabled && !this.busy ? "" : "disabled"} title="${e(this.busy ? "Wait for the current save to finish" : enabled ? "" : reason)}" ${extra}>${label}</button>`;
  }
  actions() {
    const items = this.items(),
      groups = this.selected("group"),
      leaves = this.selected("leaf");
    const one = this.selection.size === 1,
      editable = one && !leaves.length;
    const removable =
      items.length &&
      items.every(
        (item) =>
          item.kind === "leaf" &&
          this.data.leaves[item.id].parent_group_id !== null,
      );
    const reorder = (offset) =>
      items.length === 1 &&
      !!reorderOperation(this.data, items[0].kind, items[0].id, offset);
    const type =
      this.tab === "Hierarchy"
        ? "group"
        : this.tab === "Collections"
          ? "collection"
          : "preset";
    return `<div class="toolbar contextual"><span class="selection-summary">${this.selection.size ? `${this.selection.size} selected` : "Select an item to see its details"}</span>${items.length ? this.button("move", "Move to…", true) : ""}<details class="context-menu"><summary aria-label="More actions">•••</summary><div class="menu-items">
      ${this.button("rename", "Rename " + type, editable, "Select exactly one group, collection or preset")}
      ${this.tab === "Hierarchy" ? `${this.button("up", "Move up", reorder(-1), "Select one item with a previous sibling")}${this.button("down", "Move down", reorder(1), "Select one item with a next sibling")}${this.button("remove-lights", "Remove membership", removable, "Select assigned physical lights in the hierarchy")}` : ""}
      ${this.button("delete", "Delete " + type, editable && (this.tab !== "Hierarchy" || groups.length === 1), "Select exactly one group, collection or preset", 'class="danger"')}
      <p class="hint">Unavailable actions explain their required selection when focused or hovered.</p></div></details></div>`;
  }
  tree() {
    const rows = treeRows(this.data, this.expanded, this.search);
    this.stats = groupStats(this.data);
    this.firstRowKey = rows.length ? `${rows[0].kind}:${rows[0].id}` : null;
    return `<div class="tree" data-dom-key="tree" data-scroll="tree" role="tree" aria-label="Lighting hierarchy" aria-multiselectable="true" aria-busy="${this.busy}"><div class="root" data-drop="group:">${icon("folder")}<span>Installation root</span><small>Drop here to move to root</small></div>${rows.map((row) => this.treeRow(row)).join("")}${rows.length ? "" : `<div class="empty-state"><h3>${this.search ? "No matching items" : "Start with a lighting group"}</h3><p>${this.search ? "Try another name or entity ID." : "Create your first group, then add lights from the browser."}</p></div>`}</div>`;
  }
  treeRow({ kind, id, depth }) {
    const key = `${kind}:${id}`,
      group = kind === "group",
      name = this.name(kind, id);
    const light = this.data.inventory.lights.find(
      (light) => light.entity_id === this.data.routes[id],
    );
    const stats = this.stats[id],
      unavailable = !group && !["on", "off"].includes(light?.state);
    const meta = group
      ? `${stats.groups} groups · ${stats.direct} direct · ${stats.total} total lights`
      : this.data.routes[id] || "Registry entry missing";
    const state = group
      ? stats.state
      : unavailable
        ? "Unavailable"
        : light.state === "on"
          ? "On"
          : "Off";
    const first = this.firstRowKey;
    return `<div class="tree-row" role="treeitem" aria-label="${e(name)}" aria-level="${depth + 1}" aria-selected="${this.selection.has(key)}" ${group ? `aria-expanded="${this.expanded.has(id)}"` : ""} tabindex="${(this.focusKey || first) === key ? 0 : -1}" data-key="${e(key)}" data-drop="${e(key)}" data-drag="${e(key)}" draggable="true" style="--depth:${depth}">
      <button type="button" class="drag" data-drag="${e(key)}" draggable="true" aria-label="Drag ${e(name)}" title="Drag to move">⠿</button>
      ${group && stats.groups + stats.direct > 0 ? `<button type="button" class="chevron" data-action="expand" data-id="${e(id)}" aria-label="${this.expanded.has(id) ? "Collapse" : "Expand"} ${e(name)}">${icon(this.expanded.has(id) ? "down" : "right")}</button>` : `<span class="chevron spacer"></span>`}
      <span class="node-icon ${group ? "group-icon" : "light-icon"}">${icon(group ? "folder" : "light")}</span><span class="row-copy"><span class="row-name">${e(name)}</span><small class="row-meta">${e(meta)}</small></span>
      <span class="row-status"><span class="state state-${state.toLowerCase()}">${e(state)}</span>${group && stats.unavailable ? `<small class="unavailable-count">${stats.unavailable} unavailable</small>` : ""}</span></div>`;
  }
  inspector() {
    if (this.tab !== "Hierarchy") return "";
    const items = this.items();
    if (items.length !== 1)
      return `<section class="inspector" data-dom-key="inspector" data-scroll="inspector"><div class="eyebrow">Inspector</div><h3>${items.length ? `${items.length} items selected` : "A place for every light"}</h3><p class="hint">${items.length ? "Move selected items together. A selected group keeps its entire subtree." : "Select a group or light to see its details. Drag to organize, or use Move to."}</p>${items.length ? this.button("move", "Move selected items…") : ""}</section>`;
    const { kind, id } = items[0],
      name = this.name(kind, id),
      record = (kind === "group" ? this.data.groups : this.data.leaves)[id];
    const pair = (label, value) =>
      `<div class="info-pair"><dt>${label}</dt><dd>${e(value)}</dd></div>`;
    const parent = record.parent_group_id
      ? this.groupPath(record.parent_group_id)
      : kind === "group"
        ? "Installation root"
        : "Unassigned";
    let content;
    if (kind === "group") {
      const stats = this.stats[id];
      const master = Math.round(
        ((this.data.runtime.group_masters[id] ?? 255) * 100) / 255,
      );
      content = `<dl class="group-metrics">${pair("Direct groups", stats.groups)}${pair("Direct lights", stats.direct)}${pair("Total lights", stats.total)}</dl><p class="hint">${e(`${stats.total - stats.unavailable} available · ${stats.unavailable} unavailable`)}<br>${e(record.power_policy)} · Relative master ${master}%</p><details class="group-information"><summary>Group information</summary><dl>${pair("Parent", parent)}${pair("Bare ON policy", record.power_policy)}${pair("Relative master", `${master}%`)}</dl></details><div class="inspector-actions">${this.button("rename", "Edit", true, "", 'aria-label="Edit name / policy"')}${this.button("move", "Move…", true, "", 'aria-label="Move group…"')}${this.button("delete", "Delete…", true, "", 'class="danger" aria-label="Delete group…"')}</div><details class="lighting-controls"><summary>Lighting controls</summary><p class="hint">These controls operate lights. Organization and selection do not.</p><div class="controls">${this.button("group-on", "ON", !!this.data.entities[id], "Group host is missing", `data-id="${e(id)}"`)}${this.button("group-off", "OFF", !!this.data.entities[id], "Group host is missing", `data-id="${e(id)}"`)}<label>Relative master<input aria-label="${e(name)} relative master" type="range" min="1" max="100" step="1" data-master="${e(id)}" value="${Math.max(1, master)}"></label></div></details>`;
    } else {
      const light = this.data.inventory.lights.find(
        (light) => light.entity_id === this.data.routes[id],
      );
      const area =
        this.data.inventory.areas.find((area) => area.id === light?.area_id)
          ?.name || "No area";
      const labels =
        light?.labels
          .map(
            (id) =>
              this.data.inventory.labels.find((label) => label.id === id)
                ?.name || id,
          )
          .join(", ") || "No labels";
      content = `<dl>${pair("Entity ID", this.data.routes[id] || "Registry entry missing")}${pair("Parent", parent)}${pair("Area", area)}${pair("HA labels", labels)}${pair("Availability", light?.state || "Missing")}</dl><div class="inspector-actions">${this.button("move", "Move light…")}${this.button("remove-lights", "Remove from hierarchy", record.parent_group_id !== null, "This light is already unassigned")}</div>`;
    }
    return `<section class="inspector" data-dom-key="inspector" data-scroll="inspector"><div class="eyebrow">${kind === "group" ? "Group details" : "Physical light"}</div><h3>${e(name)}</h3>${content}</section>`;
  }
  browser() {
    const hierarchy = this.tab === "Hierarchy",
      lights = unassignedLights(this.data, this.filters, hierarchy),
      groups = this.selected("group");
    const destination =
      hierarchy && this.selection.size === 1 && groups.length === 1
        ? groups[0]
        : null;
    const eligible = new Set(
      unassignedLights(this.data, {}, hierarchy).map(
        (light) => light.entity_id,
      ),
    );
    const count = [...this.lightSelection].filter((id) =>
      eligible.has(id),
    ).length;
    const options = (key, items) =>
      `<option value="">All ${key}</option>` +
      items
        .map(
          (item) =>
            `<option value="${e(item.id)}" ${this.filters[key === "labels" ? "label" : key === "areas" ? "area_id" : "device_id"] === item.id ? "selected" : ""}>${e(item.name)}</option>`,
        )
        .join("");
    return `<section class="light-browser" data-dom-key="browser"><div class="eyebrow">${destination ? `Add lights to: ${e(this.name("group", destination))}` : hierarchy ? "Available to organize" : "Physical light browser"}</div><h3>${hierarchy ? "Unassigned lights" : "All physical lights"}<span class="count-badge">${lights.length}</span></h3><p class="hint">${hierarchy ? "Select lights, then Add or drag to a group." : "Lights can join multiple collections. Primary parents stay unchanged."}</p><input aria-label="Search physical lights" data-search="lights" placeholder="Name or entity ID" value="${e(this.filters.query || "")}"><details class="filter-details"><summary>Filter lights</summary><div class="filters"><label>Area<select data-filter="area_id" aria-label="Area">${options("areas", this.data.inventory.areas)}</select></label><label>HA label<select data-filter="label" aria-label="HA label">${options("labels", this.data.inventory.labels)}</select></label><label>Device<select data-filter="device_id" aria-label="Device">${options("devices", this.data.inventory.devices)}</select></label></div></details><div class="browser-selection">${this.button("select-lights", "Select all matching", lights.length > 0, "No lights match these filters")}<span data-selection-count>${count} selected</span></div><div class="member-list" data-dom-key="inventory" data-scroll="browser">${lights.map((light) => `<div class="browser-row" data-dom-key="inventory:${e(light.entity_id)}" data-drag="inventory:${e(light.entity_id)}" draggable="${hierarchy}"><button class="drag" data-drag="inventory:${e(light.entity_id)}" draggable="${hierarchy}" aria-label="Drag ${e(light.name)}" ${hierarchy ? "" : "hidden"}>⠿</button><label class="check"><input type="checkbox" data-light="${e(light.entity_id)}" ${this.lightSelection.has(light.entity_id) ? "checked" : ""}><span>${e(light.name)}<small>${e(light.entity_id)} · ${e(light.state)}</small></span></label></div>`).join("") || `<p class="empty-state">${Object.values(this.filters).some(Boolean) ? "No unassigned lights match these filters." : hierarchy ? "Every available light is assigned. Remove a light from its group to make it available here." : "No physical lights match."}</p>`}</div>${hierarchy ? `<div class="add-footer">${this.button("add-lights", `Add ${count} light${count === 1 ? "" : "s"}`, !!destination && count > 0, "Select one destination group and at least one unassigned light", 'class="primary"')}<small>${destination ? `To ${e(this.name("group", destination))}` : "Select a destination group in the tree"}</small></div>` : ""}</section>`;
  }
  collectionList() {
    const ids = this.visibleKeys().map((key) => key.split(":")[1]),
      selected = this.selected("collection");
    return `<div class="section-heading"><div><h2>Collections</h2><p>Flexible sets of lights, across any branch.</p></div>${this.button("new-collection", "Create collection", true, "", 'class="primary"')}</div><div class="search-tools"><input aria-label="Search collections" data-search="list" value="${e(this.listSearch[this.tab] || "")}" placeholder="Search collections">${this.button("edit-collection", "Edit members", selected.length === 1, "Select one collection")}</div>${this.actions()}<div class="catalog" data-dom-key="catalog" data-scroll="catalog" role="listbox" aria-label="Collections" aria-multiselectable="true">${
      ids
        .map((id) => {
          const c = this.data.collections[id];
          return `<article role="option" tabindex="0" aria-selected="${this.selection.has(`collection:${id}`)}" data-key="collection:${e(id)}"><span class="node-icon">${icon("collection")}</span><div><h3>${e(c.name)}</h3><p>${c.members.length} lights</p><small>${c.members
            .slice(0, 6)
            .map((leaf) => e(this.name("leaf", leaf)))
            .join(
              " · ",
            )}${c.members.length > 6 ? " …" : ""}</small></div></article>`;
        })
        .join("") ||
      `<div class="empty-state"><h3>${this.listSearch[this.tab] ? "No matching collections" : "Bring lights together"}</h3><p>Select lights in the browser and create a collection. A light can join more than one collection.</p></div>`
    }</div>${selected.length ? `<details class="lighting-controls"><summary>Collection lighting controls</summary><div class="toolbar">${this.button("collection-on", "ON selected")}${this.button("collection-off", "OFF selected")}<label>Brightness (%)<input name="collection-brightness" type="number" min="0.01" max="100" value="50"></label>${this.button("collection-brightness", "Apply brightness")}</div><p class="hint">Brightness is absolute and preserves OFF state.</p></details>` : ""}`;
  }
  presetList() {
    const ids = this.visibleKeys().map((key) => key.split(":")[1]),
      one = this.selected("preset").length === 1;
    return `<div class="section-heading"><div><h2>Presets</h2><p>Saved recipes. Editing never activates a preset.</p></div>${this.button("new-preset", "Create preset", true, "", 'class="primary"')}</div><div class="search-tools"><input aria-label="Search presets" data-search="list" value="${e(this.listSearch[this.tab] || "")}" placeholder="Search presets">${this.button("edit-preset", "Edit recipe", one, "Select one preset")}</div>${this.actions()}<div class="catalog" data-dom-key="catalog" data-scroll="catalog" role="listbox" aria-label="Presets" aria-multiselectable="true">${
      ids
        .map((id) => {
          const p = this.data.presets[id];
          return `<article role="option" tabindex="0" aria-selected="${this.selection.has(`preset:${id}`)}" data-key="preset:${e(id)}"><span class="node-icon">${icon("preset")}</span><div><h3>${e(p.name)}</h3><p>${p.targets.length} assignments</p>${p.targets
            .map(
              (row) =>
                `<small><b>${e(this.name(row.target.kind, row.target.id))}</b> · ${e(
                  Object.entries(row.properties)
                    .map(
                      ([key, value]) =>
                        `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`,
                    )
                    .join(" · "),
                )}</small>`,
            )
            .join("")}</div></article>`;
        })
        .join("") ||
      `<div class="empty-state"><h3>${this.listSearch[this.tab] ? "No matching presets" : "Create a lighting recipe"}</h3><p>Assign only the properties you want to change. More specific targets take precedence.</p></div>`
    }</div>${one ? `<details class="lighting-controls"><summary>Activate preset</summary><p class="hint">Activation sends lighting commands. Review the recipe before using it.</p>${this.button("activate", "Activate selected")}</details>` : ""}`;
  }
  render() {
    if (this.dialog && this.shadowRoot.querySelector("dialog[open]")) {
      this.shadowRoot.querySelector(".dialog-error").textContent =
        this.error || "";
      this.shadowRoot.querySelector('[data-action="confirm"]').disabled =
        this.busy;
      return;
    }
    const oldTab = this.renderedTab || this.tab;
    const scroll = captureScroll(this.shadowRoot);
    if (scroll.length) {
      const previous = new Map(
        (this.scrollStates[oldTab] || []).map((state) => [state.id, state]),
      );
      scroll.forEach((state) => previous.set(state.id, state));
      this.scrollStates[oldTab] = [...previous.values()];
    }
    this.renderedTab = this.tab;
    const content = !this.data
      ? `<div class="empty-state">Loading your lighting installation…</div>`
      : this.tab === "Hierarchy"
        ? `<div class="section-heading"><div><h2>Hierarchy</h2><p>Organize your lighting installation.</p></div>${this.button("new-group", "Create group", true, "", 'class="primary"')}</div><div class="search-tools"><input aria-label="Search hierarchy" data-search="tree" value="${e(this.search)}" placeholder="Search groups or lights"><div class="expand-tools">${this.button("expand-all", "Expand all")}${this.button("collapse-all", "Collapse all")}</div></div>${this.actions()}${this.tree()}`
        : this.tab === "Collections"
          ? this.collectionList()
          : this.tab === "Presets"
            ? this.presetList()
            : `<div class="section-heading"><div><h2>Settings</h2><p>Your installation is saved automatically.</p></div></div><section class="settings-card"><h3>Configuration and safety</h3><dl><div class="info-pair"><dt>Configuration revision</dt><dd>${this.data.config_revision}</dd></div><div class="info-pair"><dt>Storage schema</dt><dd>${this.data.schema_version}</dd></div><div class="info-pair"><dt>Recovery</dt><dd>${this.data.recovery_pending ? "Pending — repair required" : "No pending transaction"}</dd></div></dl><p>Each physical light has one primary parent. Collections can cross branches. Labels help you select lights; they never change memberships automatically.</p><p>Organization sends no lighting commands. Relative masters preserve power. Native HA brightness services retain their existing ON behavior.</p><a href="/config/integrations/integration/jai_lighting">Open integration settings and native editors</a></section>`;
    renderKeyed(
      this.shadowRoot,
      `<style>${styles}</style><header data-dom-key="header"><button class="sidebar-button" data-action="menu" aria-label="Open Home Assistant sidebar">☰</button><span class="brand-icon">${icon("light")}</span><div class="brand"><h1>J'ai Lighting</h1><span>Lighting installation</span></div><button data-action="refresh" ${this.busy ? "disabled" : ""} aria-label="Refresh">${icon("refresh")}<span>Refresh</span></button></header><nav data-dom-key="tabs" aria-label="Manager sections">${["Hierarchy", "Collections", "Presets", "Settings"].map((tab) => `<button data-action="tab" data-tab="${tab}" aria-current="${tab === this.tab ? "page" : "false"}">${tab}</button>`).join("")}</nav><div class="notification" data-dom-key="notice" role="status">${e(this.notice || "")}${this.undo ? this.button("undo", "Undo", this.undo.revision === this.data.config_revision, "Configuration changed in another session") : ""}</div><div class="error" data-dom-key="error" role="alert">${e(this.error || "")}</div><div class="panel-switcher" data-dom-key="panel-switcher">${this.tab !== "Settings" ? `<button data-action="panel" data-panel="hierarchy" aria-pressed="${this.panelMode === "hierarchy"}">${this.tab === "Hierarchy" ? "Hierarchy" : this.tab}</button><button data-action="panel" data-panel="lights" aria-pressed="${this.panelMode === "lights"}">Available Lights</button>` : ""}</div><div data-dom-key="workspace" class="workspace panel-${this.panelMode} ${this.tab === "Settings" ? "settings-workspace" : ""}"><main data-dom-key="main">${content}</main>${this.data && this.tab !== "Settings" ? `<aside data-dom-key="aside" aria-label="Details and light browser">${this.inspector()}${this.browser()}</aside>` : ""}</div>${this.dialog ? `<dialog aria-label="${e(this.dialog.title)}"><h2>${e(this.dialog.title)}</h2><form>${this.dialog.body}</form><p class="dialog-error error" role="alert">${e(this.error || "")}</p><footer><button data-action="cancel">Cancel</button><button class="primary" data-action="confirm">Continue / save</button></footer></dialog>` : ""}`,
    );
    if (this.data) this.configurePickers();
    restoreScroll(this.shadowRoot, this.scrollStates[this.tab] || []);
  }
}

function icon(name) {
  const paths = {
    folder:
      "M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z",
    light: "M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0c-1 1-1 2-1 3H9c0-1 0-2-1-3Z",
    down: "m6 9 6 6 6-6",
    right: "m9 6 6 6-6 6",
    refresh: "M20 8a8 8 0 1 0 0 8M20 3v5h-5",
    collection: "M4 5h6v6H4Zm10 0h6v6h-6ZM4 15h6v6H4Zm10 0h6v6h-6Z",
    preset:
      "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z",
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]}"></path></svg>`;
}
if (!customElements.get("jai-lighting-manager"))
  customElements.define("jai-lighting-manager", JaiLightingManager);
