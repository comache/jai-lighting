// Small keyed reconciler: preserve scroll containers, focus and unchanged rows.
// No framework or full-tree replacement is needed for ordinary updates.
const key = (node) =>
  node.nodeType === 1 &&
  (node.getAttribute("data-dom-key") || node.getAttribute("data-key"));
function patch(old, next) {
  if (old.nodeType !== next.nodeType || old.nodeName !== next.nodeName) {
    old.replaceWith(next);
    return next;
  }
  if (old.nodeType !== 1) {
    if (old.nodeValue !== next.nodeValue) old.nodeValue = next.nodeValue;
    return old;
  }
  // Open details are user state, unless a filter explicitly needs them open.
  const opened = old.localName === "details" && old.open;
  for (const a of [...old.attributes])
    if (!next.hasAttribute(a.name)) old.removeAttribute(a.name);
  for (const a of next.attributes)
    if (old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
  if (opened) old.open = true;
  children(old, next);
  if (old.localName === "input") {
    if (old.type === "checkbox") old.checked = next.checked;
    else if (old.value !== next.value) old.value = next.value;
  }
  if (old.localName === "select" && old.value !== next.value)
    old.value = next.value;
  return old;
}
function children(parent, next) {
  const available = [...parent.childNodes],
    keyed = new Map(available.filter(key).map((n) => [key(n), n]));
  let cursor = parent.firstChild;
  for (const child of [...next.childNodes]) {
    const id = key(child);
    const old = id
      ? keyed.get(id)
      : available.find(
          (n) =>
            !key(n) &&
            n.nodeType === child.nodeType &&
            n.nodeName === child.nodeName,
        );
    if (old) {
      available.splice(available.indexOf(old), 1);
      if (old !== cursor) parent.insertBefore(old, cursor);
      const kept = patch(old, child);
      cursor = kept.nextSibling;
    } else {
      parent.insertBefore(child, cursor);
    }
  }
  available.forEach((n) => n.remove());
}
export function renderKeyed(root, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  children(root, template.content);
}
export function captureScroll(root) {
  return [...root.querySelectorAll("[data-scroll]")]
    .filter((el) => el.clientHeight > 0)
    .map((el) => {
      const top = el.getBoundingClientRect().top;
      const rows = [...el.querySelectorAll("[data-key],[data-dom-key]")].filter(
        (n) => n.getBoundingClientRect().bottom > top,
      );
      return {
        id: el.dataset.scroll,
        top: el.scrollTop,
        anchors: rows.slice(0, 4).map((n) => ({
          key: key(n),
          offset: n.getBoundingClientRect().top - top,
        })),
      };
    });
}
export function restoreScroll(root, states) {
  for (const state of states) {
    const el = root.querySelector(`[data-scroll="${CSS.escape(state.id)}"]`);
    if (!el || !el.clientHeight) continue;
    // Preserve the first surviving visible item, including deletions above it.
    let top = state.top;
    for (const anchor of state.anchors) {
      const row = [...el.querySelectorAll("[data-key],[data-dom-key]")].find(
        (n) => key(n) === anchor.key,
      );
      if (!row) continue;
      top =
        el.scrollTop +
        row.getBoundingClientRect().top -
        el.getBoundingClientRect().top -
        anchor.offset;
      break;
    }
    el.scrollTop = Math.max(
      0,
      Math.min(top, el.scrollHeight - el.clientHeight),
    );
  }
}
