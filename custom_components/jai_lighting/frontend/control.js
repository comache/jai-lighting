import { renderKeyed } from "./dom.js";
import { escapeHtml as e } from "./model.js";
import { styles } from "./control-styles.js";
import {
  palette,
  keyOf,
  refOf,
  resolve,
  commandFor,
  childrenOf,
  reorder,
  safeArtwork,
  emptyCatalog,
} from "./control-model.js";
const icon = (name) =>
  `<ha-icon icon="${e(name)}" aria-hidden="true"></ha-icon>`;
const uid = () => crypto.randomUUID();
const defaults = (target) => ({
  id: uid(),
  target,
  title: "",
  icon: "mdi:lightbulb-group-outline",
  accent: "lavender",
  size: "medium",
  quick: [],
  quick_brightness: false,
});
class JaiControl extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.catalog = emptyCatalog();
    this.active = "";
    this.path = [];
    this.scrolls = {};
    this.pending = new Map();
    this.queues = new Map();
    this.unsubs = [];
    this.online = true;
    this.error = "";
    this.notice = "";
    this.generation = 0;
    this.shadowRoot.addEventListener("click", (ev) => this.click(ev));
    this.shadowRoot.addEventListener("input", (ev) => this.input(ev));
    this.shadowRoot.addEventListener("change", (ev) => this.change(ev));
    this.shadowRoot.addEventListener("keydown", (ev) => {
      if (
        ev.key === "Escape" &&
        (this.editor || this.path.length || this.collectionDetails)
      ) {
        this.editor = null;
        this.path = [];
        this.collectionDetails = null;
        this.render();
      }
      if (ev.key === "Tab") this.trapFocus(ev);
    });
    this.shadowRoot.addEventListener("dragstart", (ev) => {
      const card = ev.target.closest("[data-card-id]");
      if (!this.editing || !card) {
        ev.preventDefault();
        return;
      }
      this.dragId = card.dataset.cardId;
      ev.dataTransfer.setData("text/plain", this.dragId);
      ev.dataTransfer.effectAllowed = "move";
    });
    this.shadowRoot.addEventListener("dragover", (ev) => {
      if (this.editing && this.dragId && ev.target.closest("[data-card-id]"))
        ev.preventDefault();
    });
    this.shadowRoot.addEventListener("drop", (ev) => {
      const card = ev.target.closest("[data-card-id]");
      if (!this.editing || !this.dragId || !card) return;
      ev.preventDefault();
      this.moveCard(this.dragId, card.dataset.cardId);
      this.dragId = null;
    });
    this.shadowRoot.addEventListener("dragend", () => {
      this.dragId = null;
    });
    this.shadowRoot.addEventListener("pointerdown", (ev) => {
      const handle = ev.target.closest("[data-drag]");
      if (!this.editing || !handle || ev.pointerType === "mouse") return;
      ev.preventDefault();
      handle.setPointerCapture(ev.pointerId);
      this.touchDrag = {
        id: handle.dataset.drag,
        over: null,
        start: ev.clientY,
      };
    });
    this.shadowRoot.addEventListener("pointermove", (ev) => {
      if (!this.touchDrag) return;
      ev.preventDefault();
      const target = this.shadowRoot
        .elementFromPoint(ev.clientX, ev.clientY)
        ?.closest("[data-card-id]");
      this.touchDrag.over = target?.dataset.cardId;
      const canvas = this.shadowRoot.querySelector(".canvas");
      const box = canvas.getBoundingClientRect();
      if (ev.clientY > box.bottom - 50) canvas.scrollTop += 14;
      if (ev.clientY < box.top + 50) canvas.scrollTop -= 14;
    });
    this.shadowRoot.addEventListener("pointerup", () => {
      if (this.touchDrag?.over)
        this.moveCard(this.touchDrag.id, this.touchDrag.over);
      this.touchDrag = null;
    });
    this.shadowRoot.addEventListener("pointercancel", () => {
      this.touchDrag = null;
      this.pending.clear();
      this.render();
    });
  }
  setConfig(config) {
    if (config.type && config.type !== "custom:jai-control-card")
      throw new Error("Invalid J’ai Control card type");
    this.cardConfig = config;
  }
  getCardSize() {
    return 12;
  }
  getGridOptions() {
    return { columns: "full", rows: 12, min_columns: 6 };
  }
  set hass(value) {
    this._hass = value;
    const connection = value.connection;
    if (connection !== this.connection) {
      this.detach();
      this.connection = connection;
      this.online = connection?.connected !== false;
      this.ready = () => {
        this.online = true;
        this.load();
      };
      this.disconnected = () => {
        this.online = false;
        this.queues.clear();
        this.pending.clear();
        this.render();
      };
      connection?.addEventListener?.("ready", this.ready);
      connection?.addEventListener?.("disconnected", this.disconnected);
      this.load();
    } else if (!this.layout && !this.loading) this.load();
    this.schedule();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    this.clockTimer = setInterval(() => this.updateClock(), 1000);
    if (this.hass && !this.layout) this.load();
    this.render();
  }
  disconnectedCallback() {
    clearInterval(this.clockTimer);
    clearTimeout(this.refreshTimer);
    clearTimeout(this.noticeTimer);
    cancelAnimationFrame(this.frame);
    this.detach();
    this.connection = null;
    this.pending.clear();
    this.queues.clear();
  }
  detach() {
    this.generation++;
    for (const off of this.unsubs.splice(0)) off();
    this.subscribing = false;
    this.connection?.removeEventListener?.("ready", this.ready);
    this.connection?.removeEventListener?.("disconnected", this.disconnected);
  }
  schedule() {
    if (this.frame || this.editor) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      if (!this.editor) this.render();
    });
  }
  async ws(command, data = {}) {
    return this.hass.callWS({
      type: "jai_lighting/dashboard",
      command,
      ...data,
    });
  }
  async load() {
    if (!this.hass || this.loading || !this.online) return;
    this.loading = true;
    const generation = this.generation;
    try {
      const data = await this.ws("get");
      if (generation !== this.generation) return;
      this.catalog = data.catalog;
      this.path = this.path.filter((id) => this.catalog.groups[id]);
      if (!this.editing) {
        this.layout = data.config;
        this.revision = data.revision;
        this.saved = data.saved;
        this.external = false;
      } else if (data.revision !== this.revision) this.external = true;
      if (!this.active) {
        try {
          this.active =
            localStorage.getItem(`jai-control:tab:${this.hass.user?.id}`) || "";
        } catch {}
      }
      if (!this.current()?.tabs.some((t) => t.id === this.active && !t.hidden))
        this.active = this.current()?.tabs.find((t) => !t.hidden)?.id || "";
      this.error = "";
      await this.subscribe();
    } catch (err) {
      this.error = err.message || String(err);
    } finally {
      this.loading = false;
      this.render();
    }
  }
  async subscribe() {
    if (
      this.unsubs.length ||
      this.subscribing ||
      !this.connection?.subscribeEvents
    )
      return;
    this.subscribing = true;
    const gen = this.generation;
    const connection = this.connection;
    const subscriptions = [];
    try {
      for (const name of [
        "jai_lighting_manager_updated",
        "jai_lighting_dashboard_updated",
        "entity_registry_updated",
        "area_registry_updated",
        "floor_registry_updated",
      ]) {
        const off = await connection.subscribeEvents((event) => {
          if (
            name === "jai_lighting_manager_updated" &&
            event.data?.config_revision === this.catalog.topology_revision
          )
            return;
          clearTimeout(this.refreshTimer);
          this.refreshTimer = setTimeout(() => this.load(), 350);
        }, name);
        subscriptions.push(off);
        if (gen !== this.generation || !this.isConnected) return;
      }
      this.unsubs.push(...subscriptions.splice(0));
    } finally {
      for (const off of subscriptions) off();
      if (gen === this.generation) this.subscribing = false;
    }
  }
  current() {
    return this.editing ? this.draft : this.layout;
  }
  tab() {
    return (
      this.current()?.tabs.find((t) => t.id === this.active) ||
      this.current()?.tabs[0]
    );
  }
  updateClock() {
    const now = new Date();
    const time = this.shadowRoot.querySelector("[data-clock]"),
      date = this.shadowRoot.querySelector("[data-date]");
    if (time)
      time.textContent = new Intl.DateTimeFormat("ro-RO", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Europe/Bucharest",
      }).format(now);
    if (date)
      date.textContent = new Intl.DateTimeFormat("ro-RO", {
        weekday: "long",
        day: "numeric",
        month: "long",
        timeZone: "Europe/Bucharest",
      }).format(now);
  }
  flash(text, error = false) {
    this.error = error ? text : "";
    this.notice = error ? "" : text;
    clearTimeout(this.noticeTimer);
    if (!error)
      this.noticeTimer = setTimeout(() => {
        this.notice = "";
        this.render();
      }, 5000);
    this.render();
  }
  async dispatch(ref, action, value) {
    if (this.editing || !this.online) return;
    const key = keyOf(ref);
    if (this.queues.has(key) && action !== "brightness" && action !== "volume")
      return;
    const operation = { ref, action, value };
    if (this.queues.has(key)) {
      this.queues.set(key, operation);
      return;
    }
    this.queues.set(key, operation);
    try {
      while (this.queues.get(key) && this.online) {
        const next = this.queues.get(key);
        this.queues.set(key, null);
        const plan = commandFor(
          next.ref,
          next.action,
          next.value,
          this.catalog,
          this.hass.states,
        );
        if (plan.domain)
          await this.hass.callService(plan.domain, plan.service, plan.data);
        else await this.hass.callWS(plan);
      }
      if (this.online)
        this.flash(
          action === "mood" ? "Atmosfera a fost activată." : "Comandă trimisă.",
        );
    } catch (err) {
      this.queues.delete(key);
      this.flash(err.message || "Comanda nu a fost confirmată.", true);
    } finally {
      this.queues.delete(key);
      this.pending.delete(key);
      this.render();
    }
  }
  input(ev) {
    const input = ev.target;
    if (input.matches("[data-level]")) {
      this.pending.set(input.dataset.ref, Number(input.value));
      for (const output of this.shadowRoot.querySelectorAll("[data-reading]"))
        if (output.dataset.reading === input.dataset.ref)
          output.textContent = input.value;
    }
  }
  change(ev) {
    const input = ev.target;
    if (input.matches("[data-level]"))
      this.dispatch(
        refOf(input.dataset.ref),
        input.dataset.level,
        Number(input.value),
      );
  }
  refAttrs(ref) {
    return `data-ref="${e(keyOf(ref))}"`;
  }
  button(
    action,
    text,
    { ref, disabled = false, title = "", className = "" } = {},
  ) {
    disabled ||= action === "details" && this.editing;
    return `<button type="button" data-action="${action}" ${ref ? this.refAttrs(ref) : ""} ${disabled ? "disabled" : ""} class="${className}" ${title ? `aria-label="${e(title)}" title="${e(title)}"` : ""}>${text}</button>`;
  }
  power(view, ref) {
    return this.button("power", icon("mdi:power"), {
      ref,
      disabled: !view.power || !this.online || this.editing,
      className: `icon power ${view.on ? "on" : ""}`,
      title: `${view.on ? "Oprește" : "Pornește"} ${view.name}`,
    });
  }
  slider(view, ref, vertical = false) {
    if (!view.brightness) return "";
    const key = keyOf(ref),
      pct = this.pending.get(key) ?? view.pct;
    return `<input type="range" ${vertical ? 'class="vertical" orient="vertical" aria-orientation="vertical"' : ""} aria-label="Luminozitate ${e(view.name)}" aria-valuetext="${pct}%" data-level="brightness" ${this.refAttrs(ref)} min="${view.groupId ? 1 : 0}" max="100" step="1" value="${pct}" ${!view.available || !this.online || this.editing ? "disabled" : ""}>`;
  }
  mini(ref, brightness = true) {
    const view = resolve(ref, this.catalog, this.hass.states);
    return `<div class="mini" data-dom-key="mini:${e(keyOf(ref))}"><div class="mini-head">${this.button("details", `<span>${e(view.name)}<small>${!view.available ? "Indisponibil" : view.on ? "Pornit" : "Oprit"}</small></span>`, { ref, className: "name" })}${ref?.kind === "preset" ? this.button("mood", icon("mdi:play"), { ref, disabled: !view.available || this.editing || !this.online, title: `Activează ${view.name}`, className: "icon" }) : this.power(view, ref)}</div>${brightness ? this.slider(view, ref) : ""}</div>`;
  }
  media(ref) {
    const view = resolve(ref, this.catalog, this.hass.states);
    const a = view.attrs || {},
      features = a.supported_features || 0;
    const artwork = safeArtwork(a.entity_picture);
    const active =
      view.available && view.state !== "off" && view.state !== "idle";
    return `<div class="media"><div class="track">${artwork ? `<img class="art" alt="Coperta albumului" src="${e(artwork)}" loading="lazy" referrerpolicy="no-referrer">` : `<span class="art">${icon("mdi:music")}</span>`}<div class="track-info"><b>${e(a.media_title || "Spotify")}</b><small>${e(a.media_artist || (!view.available ? "Indisponibil" : "Nicio redare activă"))}</small></div></div><div class="transport">${this.button("previous", icon("mdi:skip-previous"), { ref, title: "Piesa precedentă", disabled: !active || !(features & 16) || this.editing || !this.online })}${this.button(view.state === "playing" ? "pause" : "play", icon(view.state === "playing" ? "mdi:pause" : "mdi:play"), { ref, title: view.state === "playing" ? "Pauză" : "Redare", disabled: !view.available || !(features & (view.state === "playing" ? 1 : 16384)) || this.editing || !this.online })}${this.button("next", icon("mdi:skip-next"), { ref, title: "Piesa următoare", disabled: !active || !(features & 32) || this.editing || !this.online })}</div>${features & 4 ? `<label class="volume">${icon("mdi:volume-medium")}<input aria-label="Volum ${e(view.name)}" data-level="volume" ${this.refAttrs(ref)} type="range" min="0" max="100" value="${this.pending.get(keyOf(ref)) ?? Math.round((a.volume_level || 0) * 100)}" ${!active || this.editing || !this.online ? "disabled" : ""}></label>` : ""}<small>${e(a.source || "Alege un dispozitiv în Spotify Connect.")}</small></div>`;
  }
  card(card, popup = false) {
    const ref = card.target,
      view = resolve(ref, this.catalog, this.hass.states),
      key = keyOf(ref),
      pct = this.pending.get(key) ?? view.pct;
    const attrs = this.refAttrs(ref);
    return `<article class="light-card ${e(card.size)} ${view.on ? "is-on" : ""} ${this.editing && !popup ? "edit-card" : ""}" data-dom-key="card:${e(card.id)}" data-card-id="${e(card.id)}" style="--accent:${palette[card.accent] || palette.lavender}" ${this.editing && !popup ? 'draggable="true"' : ""}>
      ${this.editing && !popup ? `<div class="edit-tools"><button class="drag-handle" data-drag="${e(card.id)}" aria-label="Trage ${e(view.name)}">${icon("mdi:drag")}</button><button data-action="edit-card" data-id="${e(card.id)}">Editează</button><button data-action="card-up" data-id="${e(card.id)}" aria-label="Mută cardul la stânga">←</button><button data-action="card-down" data-id="${e(card.id)}" aria-label="Mută cardul la dreapta">→</button></div>` : ""}
      <div class="card-heading"><span class="glyph">${icon(card.icon || "mdi:lightbulb-outline")}</span>${this.button("details", `<div><h3>${e(card.title || view.name)}</h3><div class="state">${!view.available ? "Indisponibil" : view.mixed ? "Mixt" : view.on ? "Pornit" : view.domain === "light" || view.domain === "collection" ? "Oprit" : e(view.state || "")}${view.missing ? ` · ${view.missing} indisponibile` : ""}</div></div>`, { ref, className: "name" })}</div>
      ${view.domain === "media_player" ? this.media(ref) : ref.kind === "preset" ? `<div class="fader-body">${this.button("mood", `${icon("mdi:creation-outline")} Activează`, { ref, disabled: !view.available || !this.online || this.editing })}</div>` : view.brightness ? `<div class="fader-body"><div class="reading"><strong><span data-reading="${e(key)}" style="font:inherit">${pct}</span><span>%</span></strong><small>${view.groupId ? "Master relativ" : ref.kind === "collection" ? "Selecție specială · nivel absolut" : "Luminozitate"}</small></div><div class="fader">${this.slider(view, ref, true)}</div></div>` : ["sensor", "binary_sensor", "weather"].includes(view.domain) ? `<div class="sensor-reading">${e(view.available ? view.state : "—")} <small>${e(view.attrs?.unit_of_measurement || "")}</small></div>` : `<div class="fader-body"><small>${view.available ? "Comandă ON / OFF" : "Așteaptă reconectarea dispozitivului"}</small></div>`}
      ${view.domain !== "media_player" && ref.kind !== "preset" ? `<div class="card-bottom">${this.button("details", "Detalii ↗", { ref, className: "detail" })}${view.power ? this.power(view, ref) : `<span ${attrs}></span>`}</div>` : ""}
      ${card.quick?.length ? `<div class="quick">${card.quick.map((r) => this.mini(r, card.quick_brightness)).join("")}</div>` : ""}</article>`;
  }
  choices(kinds = ["group", "entity", "collection", "preset"]) {
    return kinds
      .flatMap((kind) =>
        Object.entries(
          this.catalog[
            {
              group: "groups",
              entity: "entities",
              collection: "collections",
              preset: "presets",
            }[kind]
          ],
        ).map(([id, r]) => ({ ref: { kind, id }, name: r.name, kind })),
      )
      .sort(
        (a, b) =>
          a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name, "ro"),
      );
  }
  options(selected = "", kinds, none = false) {
    return `${none ? '<option value="">Neconfigurat</option>' : ""}${this.choices(
      kinds,
    )
      .map(
        (c) =>
          `<option value="${e(keyOf(c.ref))}" ${keyOf(c.ref) === selected ? "selected" : ""}>${e(c.name)} · ${e(c.kind === "collection" ? "Selecție specială" : c.kind)}${c.kind === "entity" ? ` · ${e(c.ref.id)}` : ""}</option>`,
      )
      .join("")}`;
  }
  multi(name, label, refs, kinds) {
    const selected = new Set(refs.map(keyOf));
    return `<label class="full">${label}<small>Alege mai multe elemente; ordinea selectată urmează lista.</small><select aria-label="${e(label)}" name="${name}" multiple>${this.choices(
      kinds,
    )
      .map(
        (c) =>
          `<option value="${e(keyOf(c.ref))}" ${selected.has(keyOf(c.ref)) ? "selected" : ""}>${e(c.name)} · ${e(c.kind)}</option>`,
      )
      .join("")}</select></label>`;
  }
  editorContent() {
    const { type, id } = this.editor,
      tab = this.tab();
    if (type === "card") {
      const card =
        tab.cards.find((c) => c.id === id) ||
        defaults(this.choices()[0]?.ref || { kind: "entity", id: "" });
      return `<div class="fields"><label class="full">Entitate sau grup<select aria-label="Entitate sau grup" name="target" required>${this.options(keyOf(card.target))}</select></label><label>Titlu opțional<input name="title" maxlength="160" value="${e(card.title)}"></label><label>Pictogramă MDI<input name="icon" pattern="mdi:[a-z0-9-]{1,80}" required value="${e(card.icon)}"></label><label>Accent<select aria-label="Accent" name="accent">${Object.keys(
        palette,
      )
        .map(
          (p) => `<option ${p === card.accent ? "selected" : ""}>${p}</option>`,
        )
        .join(
          "",
        )}</select></label><label>Dimensiune<select aria-label="Dimensiune" name="size">${["small", "medium", "wide"].map((p) => `<option ${p === card.size ? "selected" : ""}>${p}</option>`).join("")}</select></label>${this.multi("quick", "Comenzi rapide", card.quick, ["group", "entity", "collection"])}<label class="check full"><input name="quick_brightness" type="checkbox" ${card.quick_brightness ? "checked" : ""}>Luminozitate pentru comenzile rapide</label></div>${id ? `<p style="margin-top:18px">${this.button("remove-card", "Elimină din dashboard", { className: "danger" })}</p>` : ""}`;
    }
    if (type === "tabs")
      return `<div class="editor-list">${this.draft.tabs.map((t, i) => `<div class="editor-row" data-dom-key="tab:${e(t.id)}"><span>${e(t.name)} ${t.hidden ? "· ascuns" : ""}</span><button data-action="rename-tab" data-id="${e(t.id)}">Nume</button><button data-action="toggle-tab" data-id="${e(t.id)}">${t.hidden ? "Arată" : "Ascunde"}</button><button data-action="tab-up" data-id="${e(t.id)}" ${i === 0 ? "disabled" : ""} aria-label="Mută ${e(t.name)} înainte">↑</button><button data-action="tab-down" data-id="${e(t.id)}" ${i === this.draft.tabs.length - 1 ? "disabled" : ""} aria-label="Mută ${e(t.name)} după">↓</button></div>`).join("")}</div><div class="fields"><label class="full">Tab nou<input name="new-tab" maxlength="160" placeholder="Numele tabului"></label></div>`;
    if (type === "tab-name")
      return `<div class="fields"><label class="full">Numele tabului<input name="name" required maxlength="160" value="${e(this.draft.tabs.find((t) => t.id === id).name)}"></label></div>`;
    if (type === "sidebar") {
      const s = this.draft.sidebar;
      return `<div class="fields"><label class="full">Numele locației<input name="title" required maxlength="160" value="${e(this.draft.title)}"></label><label>Iluminat general<select aria-label="Iluminat general" name="general">${this.options(keyOf(s.general), ["group"], true)}</select></label><label>Service<select aria-label="Service" name="service">${this.options(keyOf(s.service), ["group"], true)}</select></label><label class="full">Spotify / media player<select aria-label="Spotify / media player" name="spotify"><option value="">Neconfigurat</option>${this.choices(
        ["entity"],
      )
        .filter(
          (c) => this.catalog.entities[c.ref.id].domain === "media_player",
        )
        .map(
          (c) =>
            `<option value="${e(keyOf(c.ref))}" ${keyOf(c.ref) === keyOf(s.spotify) ? "selected" : ""}>${e(c.name)} · ${e(c.ref.id)}</option>`,
        )
        .join(
          "",
        )}</select></label>${this.multi("shortcuts", "Comenzi rapide în sidebar", s.shortcuts, ["group", "entity", "collection"])}</div>`;
    }
    return `<div class="fields">${this.multi("moods", "Atmosfere afișate pe acest tab", tab.moods, ["preset"])}</div>`;
  }
  showEditor(type, id) {
    this.editor = { type, id };
    this.render();
    queueMicrotask(() =>
      this.shadowRoot
        .querySelector(".popup input,.popup select,.popup button")
        ?.focus(),
    );
  }
  details(ref) {
    if (this.editing) return;
    const view = resolve(ref, this.catalog, this.hass.states);
    if (view.groupId) {
      if (!this.path.includes(view.groupId)) this.path.push(view.groupId);
      this.render();
      queueMicrotask(() =>
        this.shadowRoot.querySelector(".popup-header button")?.focus(),
      );
    } else if (view.entityId)
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          bubbles: true,
          composed: true,
          detail: { entityId: view.entityId },
        }),
      );
    else if (ref?.kind === "collection") {
      this.collectionDetails = ref.id;
      this.render();
    }
  }
  trapFocus(event) {
    const popup = this.shadowRoot.querySelector(".popup");
    if (!popup) return;
    const nodes = [
      ...popup.querySelectorAll(
        "button:not(:disabled),input:not(:disabled),select,a[href]",
      ),
    ].filter((n) => n.getBoundingClientRect().height);
    const first = nodes[0],
      last = nodes.at(-1);
    if (event.shiftKey && this.shadowRoot.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && this.shadowRoot.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
  moveCard(id, before) {
    this.tab().cards = reorder(this.tab().cards, id, before);
    this.render();
  }
  async saveEditor() {
    const form = this.shadowRoot.querySelector("form");
    if (!form.reportValidity()) return;
    const data = new FormData(form),
      { type, id } = this.editor;
    const refs = (name) => data.getAll(name).map(refOf).filter(Boolean);
    if (type === "card") {
      const target = refOf(String(data.get("target") || ""));
      if (!target) {
        this.flash("Alege o entitate disponibilă în catalog.", true);
        return;
      }
      const card = {
        id: id || uid(),
        target,
        title: String(data.get("title") || "").trim(),
        icon: data.get("icon"),
        accent: data.get("accent"),
        size: data.get("size"),
        quick: refs("quick"),
        quick_brightness: data.has("quick_brightness"),
      };
      if (card.quick.length > 8) {
        this.flash("Maximum 8 comenzi rapide pe card.", true);
        return;
      }
      const index = this.tab().cards.findIndex((c) => c.id === id);
      if (index < 0) this.tab().cards.push(card);
      else this.tab().cards[index] = card;
    } else if (type === "sidebar")
      this.draft = {
        ...this.draft,
        title: String(data.get("title")).trim(),
        sidebar: {
          general: refOf(String(data.get("general"))),
          service: refOf(String(data.get("service"))),
          spotify: refOf(String(data.get("spotify"))),
          shortcuts: refs("shortcuts"),
        },
      };
    else if (type === "tabs" && String(data.get("new-tab") || "").trim()) {
      const tab = {
        id: uid(),
        name: String(data.get("new-tab")).trim(),
        hidden: false,
        cards: [],
        moods: [],
        floor_id: null,
      };
      this.draft.tabs.push(tab);
      this.active = tab.id;
    } else if (type === "tab-name")
      this.draft.tabs.find((t) => t.id === id).name = String(
        data.get("name"),
      ).trim();
    else if (type === "moods") this.tab().moods = refs("moods");
    this.editor = null;
    this.render();
  }
  async click(event) {
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.action,
      ref = refOf(button.dataset.ref || ""),
      id = button.dataset.id;
    if (
      ["power", "mood", "play", "pause", "previous", "next"].includes(action)
    ) {
      await this.dispatch(ref, action);
      return;
    }
    if (action === "details") {
      this.details(ref);
      return;
    }
    if (action === "tab") {
      this.scrolls[this.active] =
        this.shadowRoot.querySelector(".canvas")?.scrollTop || 0;
      this.active = id;
      try {
        localStorage.setItem(`jai-control:tab:${this.hass.user?.id}`, id);
      } catch {}
      this.path = [];
      this.render();
      this.shadowRoot.querySelector(".canvas").scrollTop =
        this.scrolls[id] || 0;
      return;
    }
    if (action === "designer") {
      this.draft = structuredClone(this.layout);
      this.editing = true;
      this.path = [];
      this.render();
      return;
    }
    if (action === "cancel-design") {
      this.editing = false;
      this.editor = null;
      this.load();
      return;
    }
    if (action === "save-design") {
      if (this.saving) return;
      this.saving = true;
      this.render();
      try {
        const result = await this.ws("save", {
          config: this.draft,
          revision: this.revision,
        });
        this.layout = result.config;
        this.revision = result.revision;
        this.saved = true;
        this.editing = false;
        this.editor = null;
        this.external = false;
        this.flash("Design salvat.");
      } catch (err) {
        this.flash(err.message || String(err), true);
      } finally {
        this.saving = false;
        this.render();
      }
      return;
    }
    if (action === "close") {
      this.path = [];
      this.collectionDetails = null;
      this.editor = null;
      this.render();
      return;
    }
    if (action === "breadcrumb") {
      this.path = this.path.slice(0, Number(id) + 1);
      this.render();
      return;
    }
    if (action === "new-card" || action === "edit-card") {
      this.showEditor("card", id);
      return;
    }
    if (
      action === "edit-sidebar" ||
      action === "edit-tabs" ||
      action === "edit-moods"
    ) {
      this.showEditor(action.slice(5));
      return;
    }
    if (action === "editor-save") {
      this.saveEditor();
      return;
    }
    if (action === "remove-card") {
      this.tab().cards = this.tab().cards.filter(
        (c) => c.id !== this.editor.id,
      );
      this.editor = null;
      this.render();
      return;
    }
    if (action === "rename-tab") {
      this.showEditor("tab-name", id);
      return;
    }
    if (action === "toggle-tab") {
      const tab = this.draft.tabs.find((t) => t.id === id);
      if (
        !tab.hidden &&
        this.draft.tabs.filter((t) => !t.hidden).length === 1
      ) {
        this.flash("Păstrează cel puțin un tab vizibil.", true);
        return;
      }
      tab.hidden = !tab.hidden;
      if (this.active === id && tab.hidden)
        this.active = this.draft.tabs.find((t) => !t.hidden).id;
      this.render();
      return;
    }
    if (action === "tab-up" || action === "tab-down") {
      const i = this.draft.tabs.findIndex((t) => t.id === id),
        j = i + (action === "tab-up" ? -1 : 1);
      if (j >= 0 && j < this.draft.tabs.length)
        [this.draft.tabs[i], this.draft.tabs[j]] = [
          this.draft.tabs[j],
          this.draft.tabs[i],
        ];
      this.render();
      return;
    }
    if (action === "card-up" || action === "card-down") {
      const cards = this.tab().cards,
        i = cards.findIndex((c) => c.id === id),
        j = i + (action === "card-up" ? -1 : 1);
      if (j >= 0 && j < cards.length)
        [cards[i], cards[j]] = [cards[j], cards[i]];
      this.render();
      return;
    }
    if (action === "retry") {
      this.load();
      return;
    }
    if (action === "dismiss") {
      this.error = "";
      this.notice = "";
      this.render();
      return;
    }
    if (action === "menu") {
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
      return;
    }
    if (action === "fullscreen") {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else if (this.requestFullscreen) await this.requestFullscreen();
        else this.flash("Folosește Adaugă pe ecranul principal în Safari.");
      } catch {
        this.flash(
          "Modul ecran complet nu este disponibil în acest browser.",
          true,
        );
      }
      return;
    }
  }
  render() {
    const editorKey = this.editor
      ? `${this.editor.type}:${this.editor.id || "new"}`
      : "";
    const formValues =
      editorKey && editorKey === this.renderedEditorKey
        ? [...(this.shadowRoot.querySelector("form")?.elements || [])]
            .filter((el) => el.name)
            .map((el) => ({
              name: el.name,
              value: el.value,
              checked: el.checked,
              selected: el.multiple
                ? [...el.selectedOptions].map((o) => o.value)
                : null,
            }))
        : [];
    this.renderedEditorKey = editorKey;
    const config = this.current();
    if (!this.hass || !config) {
      renderKeyed(
        this.shadowRoot,
        `<style>${styles}</style><div class="empty"><h2>J'ai Control</h2><p>${e(this.error || "Se încarcă instalația…")}</p>${this.button("retry", "Reîncarcă")}</div>`,
      );
      return;
    }
    const tab = this.tab(),
      s = config.sidebar;
    let popup = "";
    if (this.editor)
      popup = `<div class="scrim"><section class="popup" role="dialog" aria-modal="true" aria-label="Designer"><div class="popup-header"><h2>Designer · ${e({ card: "Card", tabs: "Taburi", "tab-name": "Nume tab", sidebar: "Sidebar", moods: "Atmosfere" }[this.editor.type])}</h2>${this.button("close", icon("mdi:close"), { title: "Închide editorul", className: "icon" })}</div><div class="popup-body"><form>${this.editorContent()}</form></div><footer class="popup-footer">${this.button("close", "Anulează")}${this.button("editor-save", "Aplică în previzualizare", { className: "primary" })}</footer></section></div>`;
    else if (this.path.length || this.collectionDetails) {
      const id = this.path.at(-1),
        group = this.catalog.groups[id],
        collection = this.catalog.collections[this.collectionDetails];
      const refs = group
        ? childrenOf(id, this.catalog)
        : (collection?.entities || []).map((id) => ({ kind: "entity", id }));
      popup = `<div class="scrim"><section class="popup" role="dialog" aria-modal="true" aria-label="Detalii grup"><div class="popup-header"><h2>${e(group?.name || collection?.name || "Indisponibil")}</h2>${this.button("close", icon("mdi:close"), { title: "Închide detalii", className: "icon" })}</div><div class="popup-body" data-dom-key="popup-scroll"><nav class="breadcrumbs" aria-label="Navigare grup">${this.path.map((part, i) => `<button data-action="breadcrumb" data-id="${i}">${e(this.catalog.groups[part]?.name || "Grup eliminat")}</button>${i < this.path.length - 1 ? "›" : ""}`).join("")}</nav><div class="grid">${refs.map((ref) => this.card({ ...defaults(ref), id: keyOf(ref) }, true)).join("") || '<div class="empty">Acest grup nu are subgrupuri sau lumini directe.</div>'}</div></div></section></div>`;
    }
    renderKeyed(
      this.shadowRoot,
      `<style>${styles}</style><div class="shell ${this.editing ? "editing" : ""}" data-dom-key="shell"><aside class="sidebar" aria-label="Panou permanent" data-dom-key="sidebar"><div class="identity"><span class="mark">J</span><h1>${e(config.title)}</h1></div><div class="clock"><time data-clock></time><p data-date></p></div><section class="section"><h2>Iluminat general</h2>${s.general ? this.mini(s.general) : "<small>Alege grupul în Designer.</small>"}</section><section class="section"><h2>Service</h2>${s.service ? this.mini(s.service) : "<small>Niciun grup selectat.</small>"}</section><section class="section music-section"><h2>Spotify</h2>${this.media(s.spotify)}</section>${s.shortcuts.length ? `<section class="section"><h2>Comenzi rapide</h2>${s.shortcuts.map((r) => this.mini(r)).join("")}</section>` : ""}<div class="statusline"><span class="dot ${this.online ? "" : "offline"}"></span>${this.online ? "Conectat la Home Assistant" : "Conexiune întreruptă"}</div><div class="link-button">${this.button("menu", icon("mdi:menu"), { title: "Navigare Home Assistant", className: "icon" })}${this.hass.user?.is_admin ? '<a href="/jai-lighting">Light Manager ↗</a>' : ""}</div></aside><main class="operational" data-dom-key="main"><header class="topbar"><div><p class="eyebrow">Spațiul tău, în armonie</p><h1>J'ai Control</h1></div><div class="tools">${this.button("menu", icon("mdi:menu"), { title: "Navigare Home Assistant", className: "icon mobile-menu" })}${this.button("fullscreen", icon("mdi:fullscreen"), { title: "Ecran complet", className: "icon" })}${this.hass.user?.is_admin && !this.editing ? this.button("designer", `${icon("mdi:tune-variant")}<span class="label">Designer</span>`, { title: "Deschide Designer", disabled: !this.online }) : ""}</div></header><nav class="tabs" role="tablist" aria-label="Zone" data-dom-key="tabs">${config.tabs
        .filter((t) => !t.hidden)
        .map(
          (t) =>
            `<button role="tab" aria-selected="${t.id === this.active}" data-action="tab" data-id="${e(t.id)}">${e(t.name)}</button>`,
        )
        .join(
          "",
        )}</nav>${!this.online ? '<div class="offline-banner" role="status">Conexiune întreruptă. Comenzile sunt suspendate și nu vor fi retrimise automat.</div>' : ""}${this.editing ? `<div class="designer-banner"><span>${this.external ? "Configurația s-a schimbat în altă sesiune. Reîncarcă înainte de salvare." : "Previzualizare · comenzile fizice sunt dezactivate."}</span>${this.button("edit-tabs", "Taburi")}${this.button("edit-sidebar", "Sidebar")}${this.button("cancel-design", "Anulează")}${this.button("save-design", this.saving ? "Se salvează…" : "Salvează design", { className: "primary", disabled: this.saving || !this.online || this.external })}</div>` : ""}<div class="canvas" data-dom-key="canvas"><div class="section-title"><div><h2>${e(tab?.name || "Dashboard")}</h2><p>${tab?.cards.length || 0} comenzi · ${this.editing ? "aranjează cardurile prin tragere" : "control independent, într-un singur loc"}</p></div>${this.editing ? this.button("new-card", `${icon("mdi:plus")} Card`) : ""}</div><div class="grid">${tab?.cards.map((c) => this.card(c)).join("") || '<div class="empty"><h3>Un spațiu pentru ideile tale</h3>Nu există comenzi configurate aici. Adaugă entități din Designer.</div>'}</div><section class="moods"><div class="section-title"><h3>Atmosfere</h3>${this.editing ? this.button("edit-moods", "Alege atmosfere") : ""}</div><div class="mood-list">${
        tab?.moods
          .map((ref) => {
            const v = resolve(ref, this.catalog, this.hass.states);
            return this.button(
              "mood",
              `${icon("mdi:creation-outline")} ${e(v.name)}`,
              { ref, disabled: !v.available || this.editing || !this.online },
            );
          })
          .join("") ||
        "<small>Nicio atmosferă selectată pentru acest tab.</small>"
      }</div></section></div></main></div>${this.error || this.notice ? `<div class="notice ${this.error ? "error" : ""}" role="${this.error ? "alert" : "status"}">${e(this.error || this.notice)}${this.button("dismiss", "×", { title: "Închide mesajul" })}</div>` : ""}${popup}`,
    );
    for (const stored of formValues) {
      const el = this.shadowRoot.querySelector(
        `form [name="${CSS.escape(stored.name)}"]`,
      );
      if (!el) continue;
      if (stored.selected) {
        for (const option of el.options)
          option.selected = stored.selected.includes(option.value);
      } else if (el.type === "checkbox") el.checked = stored.checked;
      else if (el.value !== stored.value) el.value = stored.value;
    }
    this.updateClock();
  }
}
if (!customElements.get("jai-control"))
  customElements.define("jai-control", JaiControl);
if (!customElements.get("jai-control-card"))
  customElements.define("jai-control-card", class extends JaiControl {});
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "jai-control-card"))
  window.customCards.push({
    type: "jai-control-card",
    name: "J'ai Control",
    description: "Dashboard operațional și Designer vizual",
    preview: false,
  });
