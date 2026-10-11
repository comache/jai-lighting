import { escapeHtml as e, treeRows, siblingIds } from "./model.js";
import { renderKeyed } from "./dom.js";

// Shared form-associated picker for every group destination and preset target.
class GroupPicker extends HTMLElement {
  static formAssociated = true;
  static observedAttributes = ["value"];
  attributeChangedCallback() {
    const value = this.getAttribute("value") || "";
    if (this._value === value) return;
    this._value = value;
    if (this.trigger) this.render();
  }
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.internals = this.attachInternals();
    this.expanded = new Set();
    this.query = "";
    this.config = { groups: {}, allowRoot: true };
    this.shadowRoot.addEventListener("click", (event) => {
      event.stopPropagation();
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      if (button.dataset.toggle !== undefined) {
        this.expanded.has(button.dataset.toggle)
          ? this.expanded.delete(button.dataset.toggle)
          : this.expanded.add(button.dataset.toggle);
        this.render();
        return;
      }
      if (button.dataset.choose !== undefined) {
        this.value = button.dataset.choose;
        this.popup.hidePopover();
        this.trigger.focus();
        this.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      if (button === this.trigger) this.show();
    });
    this.shadowRoot.addEventListener("input", (event) => {
      this.query = event.target.value;
      this.render();
    });
    this.shadowRoot.addEventListener("keydown", (event) =>
      this.keyboard(event),
    );
    this.shadowRoot.addEventListener(
      "toggle",
      (event) => {
        if (event.target === this.popup)
          this.trigger.setAttribute(
            "aria-expanded",
            String(event.newState === "open"),
          );
      },
      true,
    );
    this.reposition = () => {
      if (this.popup?.matches(":popover-open")) this.position();
    };
  }
  connectedCallback() {
    this._value = this.getAttribute("value") || "";
    this.render();
    window.addEventListener("resize", this.reposition);
    window.visualViewport?.addEventListener("resize", this.reposition);
    window.visualViewport?.addEventListener("scroll", this.reposition);
    this.eventRoot = this.getRootNode();
    this.eventRoot.addEventListener("scroll", this.reposition, true);
  }
  disconnectedCallback() {
    window.removeEventListener("resize", this.reposition);
    window.visualViewport?.removeEventListener("resize", this.reposition);
    window.visualViewport?.removeEventListener("scroll", this.reposition);
    this.eventRoot?.removeEventListener("scroll", this.reposition, true);
  }
  configure(config) {
    this.config = config;
    if (!this.initialized) {
      this.expanded = new Set(Object.keys(config.groups));
      this.initialized = true;
    }
    this.updateValidity();
    this.render();
  }
  get value() {
    return this._value || "";
  }
  set value(value) {
    this._value = value || "";
    this.setAttribute("value", this._value);
    this.updateValidity();
    this.render();
  }
  get trigger() {
    return this.shadowRoot.querySelector(".trigger");
  }
  get popup() {
    return this.shadowRoot.querySelector("[popover]");
  }
  updateValidity() {
    const value = this.value,
      id = this.config.prefix ? value.replace(/^group:/, "") : value;
    const valid =
      (!value && this.config.allowRoot) ||
      (!!this.config.groups[id] && !this.config.blocked?.has(id)) ||
      this.config.extras?.some((x) => x.value === value);
    this.internals.setFormValue(value);
    this.internals.setValidity(
      valid ? {} : { customError: true },
      valid ? "" : "Choose an available destination",
      this.trigger,
    );
  }
  rows() {
    return treeRows(
      {
        groups: this.config.groups,
        leaves: {},
        routes: {},
        inventory: { lights: [] },
      },
      this.expanded,
      this.query,
    );
  }
  show() {
    this.popup.showPopover();
    this.trigger.setAttribute("aria-expanded", "true");
    this.position();
    this.shadowRoot.querySelector("input").focus();
  }
  position() {
    const r = this.trigger.getBoundingClientRect(),
      popup = this.popup;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0,
      top = viewport?.offsetTop || 0;
    const vw = viewport?.width || innerWidth,
      vh = viewport?.height || innerHeight;
    const width = Math.min(Math.max(r.width, 300), vw - 24);
    const below = top + vh - r.bottom - 12,
      above = r.top - top - 12;
    const height = Math.max(
      0,
      Math.min(vh - 24, vh * 0.55, Math.max(below, above)),
    );
    popup.style.width = `${width}px`;
    popup.style.maxHeight = `${height}px`;
    popup.style.minHeight = `${Math.min(120, height)}px`;
    popup.style.left = `${Math.max(left + 12, Math.min(r.left, left + vw - width - 12))}px`;
    const actualHeight = Math.min(popup.scrollHeight, height);
    const desired = below >= above ? r.bottom + 4 : r.top - actualHeight - 4;
    popup.style.top = `${Math.max(top + 12, Math.min(desired, top + vh - actualHeight - 12))}px`;
  }
  keyboard(event) {
    if (!this.popup.matches(":popover-open")) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        this.show();
      }
      return;
    }
    const buttons = [
        ...this.shadowRoot.querySelectorAll("[data-choose]:not(:disabled)"),
      ],
      index = buttons.indexOf(event.target);
    let destination;
    if (event.key === "ArrowDown")
      destination = buttons[Math.min(index + 1, buttons.length - 1)];
    if (event.key === "ArrowUp") destination = buttons[Math.max(index - 1, 0)];
    if (event.key === "Home") destination = buttons[0];
    if (event.key === "End") destination = buttons.at(-1);
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      this.popup.hidePopover();
      this.trigger.focus();
    }
    const id = event.target.dataset.group;
    if (id && ["ArrowRight", "ArrowLeft"].includes(event.key)) {
      event.preventDefault();
      event.key === "ArrowRight"
        ? this.expanded.add(id)
        : this.expanded.delete(id);
      this.render();
    }
    if (destination) {
      event.preventDefault();
      destination.focus();
      destination.scrollIntoView({ block: "nearest" });
    }
  }
  render() {
    const {
      groups,
      blocked = new Set(),
      allowRoot,
      prefix,
      extras = [],
    } = this.config;
    const id = prefix ? this.value.replace(/^group:/, "") : this.value;
    const label =
      groups[id]?.name ||
      extras.find((x) => x.value === this.value)?.name ||
      (allowRoot ? "Root / Unassigned" : "Choose a target");
    const open = this.popup?.matches(":popover-open");
    renderKeyed(
      this.shadowRoot,
      `<style>:host{display:block;min-width:0;font:inherit;color:inherit}*{box-sizing:border-box}button,input{font:inherit;color:inherit}button{cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}.trigger{width:100%;padding:11px;border-radius:8px;border:1px solid var(--line);background:var(--primary-background-color);text-align:left;min-height:44px;display:flex;justify-content:space-between;gap:12px}.popup{position:fixed;inset:auto;margin:0;padding:10px;border:1px solid var(--line);border-radius:12px;background:var(--surface);color:inherit;box-shadow:0 16px 48px #0004;overflow:hidden;min-height:120px}.popup:popover-open{display:flex;flex-direction:column}.popup input{flex-shrink:0;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--primary-background-color);width:100%;min-height:42px}.choices{overflow:auto;min-height:0;overscroll-behavior:contain}.entry{display:flex;align-items:center;padding-left:calc(min(var(--depth),20)*14px);min-height:44px}.entry.root{border-bottom:1px solid var(--line);margin-bottom:6px}.entry button{background:transparent;border:0;border-radius:6px;min-height:40px}.chevron,.spacer{width:28px;flex-shrink:0}.choice{display:flex;gap:8px;align-items:center;flex:1;text-align:left;padding:8px;min-width:0}.choice span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.choice[aria-selected=true]{background:var(--selected);color:var(--accent);font-weight:600}.folder{width:18px;height:18px;flex-shrink:0;color:var(--accent)}.section{font-size:11px;color:var(--muted);margin:12px 8px 4px}</style><button type="button" class="trigger" aria-label="${e(this.getAttribute("aria-label") || "Group")}" aria-haspopup="tree" aria-expanded="${!!open}"><span>${e(label)}</span><span>⌄</span></button><div class="popup" popover="auto" data-dom-key="popup"><input aria-label="Search groups" placeholder="Search groups or targets" value="${e(this.query)}"><div class="choices" role="tree" aria-label="Group destinations">${allowRoot ? '<div class="entry root" data-dom-key="root"><button type="button" class="choice" role="treeitem" data-choose="" aria-selected="' + !this.value + '">○ Root / Unassigned</button></div>' : ""}${this.rows()
        .map(
          ({ id, depth }) =>
            `<div class="entry" data-dom-key="picker:${e(id)}" style="--depth:${depth}">${siblingIds({ groups }, "group", id).length ? `<button type="button" class="chevron" data-toggle="${e(id)}" aria-label="${this.expanded.has(id) || this.query ? "Collapse" : "Expand"} ${e(groups[id].name)}">${this.expanded.has(id) || this.query ? "▾" : "▸"}</button>` : '<span class="spacer"></span>'}<button type="button" role="treeitem" class="choice" data-choose="${prefix ? "group:" : ""}${e(id)}" data-group="${e(id)}" aria-level="${depth + 1}" ${siblingIds({ groups }, "group", id).length ? `aria-expanded="${!!this.query || this.expanded.has(id)}"` : ""} aria-selected="${this.value === (prefix ? "group:" : "") + id}" ${blocked.has(id) ? 'disabled title="A group cannot move into its own subtree"' : ""}><svg class="folder" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 7h7l2 2h9v11H3Z"/></svg><span>${e(groups[id].name)}</span>${this.value === (prefix ? "group:" : "") + id ? "✓" : ""}</button></div>`,
        )
        .join("")}${extras
        .filter((x) =>
          x.name.toLocaleLowerCase().includes(this.query.toLocaleLowerCase()),
        )
        .map(
          (x) =>
            `<div class="entry" data-dom-key="${e(x.value)}"><button type="button" role="treeitem" class="choice" data-choose="${e(x.value)}" aria-selected="${this.value === x.value}"><span>${e(x.kind)} · ${e(x.name)}</span></button></div>`,
        )
        .join("")}</div></div>`,
    );
    this.updateValidity();
    if (open) this.position();
  }
}
customElements.define("jai-group-picker", GroupPicker);
