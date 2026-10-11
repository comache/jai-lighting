// Ephemeral automated fixture browser; no authenticated profile or HA connection.
import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium, webkit } from "playwright";
import { previewServer } from "../../scripts/preview-control.mjs";
let browser, server, url;
before(async () => {
  server = previewServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  url = `http://127.0.0.1:${server.address().port}`;
  const engine =
    process.env.JAI_BROWSER_ENGINE === "webkit" ? webkit : chromium;
  browser = await engine.launch({
    headless: true,
    ...(engine === chromium && process.env.JAI_BROWSER_CHANNEL
      ? { channel: process.env.JAI_BROWSER_CHANNEL }
      : {}),
  });
});
after(async () => {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
});
async function fixture(width = 1024, height = 768, touch = false) {
  const context = await browser.newContext({
    viewport: { width, height },
    hasTouch: touch,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.route("**/*", (route) =>
    route.request().url().startsWith(url) ? route.continue() : route.abort(),
  );
  await page.goto(url);
  await page.getByRole("tab", { name: "Interior", exact: true }).waitFor();
  return { page, context, errors };
}
const card = (p, id) => p.locator(`[data-card-id="${id}"]`);
const commands = (p) =>
  p.evaluate(() =>
    window.requests.filter((r) => r.domain || r.command === "collection"),
  );
async function range(page, locator, value) {
  await locator.fill(String(value));
  await page.waitForFunction(() => window.panel.queues.size === 0);
}
async function design(page) {
  await page
    .getByRole("button", { name: "Deschide Designer", exact: true })
    .click();
}
async function apply(page) {
  await page
    .getByRole("button", { name: "Aplică în previzualizare", exact: true })
    .click();
}
for (const [w, h] of [
  [1024, 768],
  [1180, 820],
  [768, 1024],
  [390, 844],
  [1440, 900],
]) {
  test(`operational layout ${w}x${h} stays within viewport with fixed tabs and sidebar`, async () => {
    const { page, context, errors } = await fixture(w, h, true);
    const b = await page.locator("jai-control").evaluate((el) => {
      const root = el.shadowRoot,
        s = root.querySelector(".shell"),
        canvas = root.querySelector(".canvas");
      return {
        width: s.scrollWidth,
        client: s.clientWidth,
        height: s.getBoundingClientRect().height,
        canvasHeight: canvas.clientHeight,
        sidebarHeight: root.querySelector(".sidebar").clientHeight,
        tabsTop: root.querySelector(".tabs").getBoundingClientRect().top,
      };
    });
    assert(b.width <= b.client);
    assert(b.height <= h + 1);
    assert(b.canvasHeight > 140);
    assert(b.sidebarHeight > 100);
    const before = await page.locator(".tabs").boundingBox();
    await page.locator(".canvas").evaluate((el) => (el.scrollTop = 500));
    const after = await page.locator(".tabs").boundingBox();
    assert.equal(before.y, after.y);
    assert.equal(await page.locator("input.vertical").count(), 4);
    assert.equal(
      await page.getByLabel("Luminozitate Spot", { exact: true }).count(),
      0,
    );
    assert.deepEqual(await commands(page), []);
    assert.deepEqual(errors, []);
    await context.close();
  });
}
test("clock uses Romanian/Bucharest formatting and updates without commands", async () => {
  const { page, context } = await fixture();
  const expected = await page.evaluate(() =>
    new Intl.DateTimeFormat("ro-RO", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Bucharest",
    }).format(new Date()),
  );
  assert.equal(await page.locator("[data-clock]").innerText(), expected);
  assert((await page.locator("[data-date]").innerText()).length > 10);
  await page.evaluate(() => {
    window.panel.shadowRoot.querySelector("[data-clock]").textContent = "stale";
  });
  await page.waitForFunction(
    () =>
      window.panel.shadowRoot.querySelector("[data-clock]").textContent !==
      "stale",
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("vertical native slider upward increases an OFF master without power command", async () => {
  const { page, context } = await fixture();
  const slider = card(page, "lounge").getByLabel("Luminozitate Lounge", {
    exact: true,
  });
  assert.equal(await slider.getAttribute("aria-orientation"), "vertical");
  await slider.focus();
  await slider.press("ArrowUp");
  await page.waitForFunction(() =>
    window.requests.some((r) => r.service === "set_master"),
  );
  const calls = await commands(page);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].service, "set_master");
  assert(calls[0].data.brightness_pct > 70);
  assert.equal(
    await page.evaluate(() => window.states["light.synthetic_lounge"].state),
    "off",
  );
  await context.close();
});
test("a drag previews locally and releases one final relative value", async () => {
  const { page, context } = await fixture();
  const slider = card(page, "lounge").getByLabel("Luminozitate Lounge", {
    exact: true,
  });
  const box = await slider.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.65);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.15, {
    steps: 12,
  });
  assert.equal((await commands(page)).length, 0);
  await page.mouse.up();
  await page.waitForFunction(() =>
    window.requests.some((r) => r.service === "set_master"),
  );
  assert.equal((await commands(page)).length, 1);
  await context.close();
});
test("queued brightness changes coalesce and retain the final value", async () => {
  const { page, context } = await fixture();
  await page.evaluate(() => (window.delay = 120));
  const slider = card(page, "lounge").getByLabel("Luminozitate Lounge", {
    exact: true,
  });
  for (const n of [20, 40, 65, 87]) {
    await slider.fill(String(n));
  }
  await page.waitForFunction(() => window.panel.queues.size === 0);
  const calls = await commands(page);
  assert(calls.length <= 3);
  assert.equal(calls.at(-1).data.brightness_pct, 87);
  await context.close();
});
test("power, quick controls and physical lights select the intended single target", async () => {
  const { page, context } = await fixture();
  await card(page, "lounge")
    .getByRole("button", { name: "Pornește Lounge", exact: true })
    .click();
  await card(page, "lounge")
    .getByRole("button", { name: "Pornește Accent", exact: true })
    .click();
  await range(
    page,
    card(page, "glow").getByLabel("Luminozitate Glow", { exact: true }),
    40,
  );
  const calls = await commands(page);
  assert.deepEqual(
    calls.map((c) => c.data.entity_id),
    [
      "light.synthetic_lounge",
      "light.synthetic_accent",
      "light.synthetic_glow",
    ],
  );
  assert.equal(calls[2].domain, "light");
  assert.equal(calls[2].service, "turn_on");
  await context.close();
});
test("recursive details show immediate children, preserve path on state changes and navigate back", async () => {
  const { page, context } = await fixture();
  await card(page, "lounge")
    .getByRole("button", { name: /^Lounge / })
    .click();
  const popup = page.getByRole("dialog", { name: "Detalii grup" });
  assert.equal(await popup.locator("[data-card-id]").count(), 2);
  assert.equal(
    await popup.getByRole("heading", { name: "Spot", exact: true }).count(),
    0,
  );
  await popup.getByRole("button", { name: /^Accent / }).click();
  assert.equal(
    await popup.getByRole("heading", { name: "Spot", exact: true }).count(),
    1,
  );
  await page.evaluate(() => window.refresh());
  assert.equal(
    await popup.getByRole("heading", { name: "Spot", exact: true }).count(),
    1,
  );
  await popup.getByRole("button", { name: "Lounge", exact: true }).click();
  assert.equal(await popup.locator("[data-card-id]").count(), 2);
  await page
    .getByRole("button", { name: "Închide detalii", exact: true })
    .click();
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("tabs retain independent scroll and sidebar DOM identity", async () => {
  const { page, context } = await fixture();
  await page.evaluate(
    () =>
      (window.oldSidebar = window.panel.shadowRoot.querySelector(".sidebar")),
  );
  await page.locator(".canvas").evaluate((el) => (el.scrollTop = 180));
  const scroll = await page.locator(".canvas").evaluate((el) => el.scrollTop);
  await page.getByRole("tab", { name: "Terasă", exact: true }).click();
  assert.equal(await card(page, "terrace").count(), 1);
  await page.getByRole("tab", { name: "Interior", exact: true }).click();
  assert.equal(
    await page.locator(".canvas").evaluate((el) => el.scrollTop),
    scroll,
  );
  assert(
    await page.evaluate(
      () =>
        window.oldSidebar === window.panel.shadowRoot.querySelector(".sidebar"),
    ),
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("mood activation is explicit; collections use absolute endpoint", async () => {
  const { page, context } = await fixture();
  await page.getByRole("button", { name: "Evening", exact: true }).click();
  await range(
    page,
    card(page, "upper").getByLabel("Luminozitate Upper accents", {
      exact: true,
    }),
    35,
  );
  const calls = await commands(page);
  assert.equal(calls[0].service, "activate_preset");
  assert.equal(calls[1].command, "collection");
  assert.deepEqual(calls[1].properties, { brightness_pct: 35 });
  await context.close();
});
test("unavailable Spotify is graceful and no unsupported controls are active", async () => {
  const { page, context } = await fixture();
  await page.evaluate(() => {
    window.states["media_player.synthetic_music"].state = "unavailable";
    window.refresh();
  });
  await page.waitForFunction(
    () =>
      window.panel.shadowRoot.querySelector('[data-action="play"]').disabled,
  );
  assert(
    await page
      .getByRole("button", { name: "Redare", exact: true })
      .isDisabled(),
  );
  assert(
    await page
      .getByLabel("Volum Spotify example", { exact: true })
      .isDisabled(),
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("Spotify transport and volume dispatch only explicit supported actions", async () => {
  const { page, context } = await fixture();
  await page.getByRole("button", { name: "Redare", exact: true }).click();
  await page.getByRole("button", { name: "Pauză", exact: true }).waitFor();
  await range(
    page,
    page.getByLabel("Volum Spotify example", { exact: true }),
    42,
  );
  assert.deepEqual(
    (await commands(page)).map((c) => c.service),
    ["media_play", "volume_set"],
  );
  await context.close();
});
test("Designer config survives reload without any lighting or registry writes", async () => {
  const { page, context } = await fixture();
  await design(page);
  await page.getByRole("button", { name: "Sidebar", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Numele locației", exact: true })
    .fill("Saved venue");
  await apply(page);
  await page
    .getByRole("button", { name: "Salvează design", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Deschide Designer", exact: true })
    .waitFor();
  await page.reload();
  await page
    .getByRole("heading", { name: "Saved venue", exact: true })
    .waitFor();
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("Designer creates custom tabs and cards from real discovery options, edits size/accent/quick controls", async () => {
  const { page, context } = await fixture();
  await design(page);
  await page.getByRole("button", { name: "Taburi", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Tab nou", exact: true })
    .fill("Audio & sensors");
  await apply(page);
  await page
    .getByRole("tab", { name: "Audio & sensors", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Card", exact: true }).click();
  await page
    .getByLabel("Entitate sau grup", { exact: true })
    .selectOption("group:accent");
  await page
    .getByLabel("Titlu opțional", { exact: true })
    .fill("Direct nested group");
  await page.getByLabel("Dimensiune", { exact: true }).selectOption("wide");
  await page.getByLabel("Accent", { exact: true }).selectOption("mint");
  await page.locator("select[name=quick]").selectOption(["group:lounge"]);
  await apply(page);
  assert.equal(await page.locator("article.wide").count(), 1);
  await page
    .getByRole("button", { name: "Salvează design", exact: true })
    .click();
  const saved = await page.evaluate(
    () => window.saved.config.tabs.at(-1).cards[0],
  );
  assert.deepEqual(saved.target, { kind: "group", id: "accent" });
  assert.deepEqual(saved.quick, [{ kind: "group", id: "lounge" }]);
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("Designer supports native drag and accessible ordering; cancel preserves saved layout", async () => {
  const { page, context } = await fixture();
  await design(page);
  await card(page, "stage").dragTo(card(page, "lounge"));
  assert.equal(
    await page.locator("[data-card-id]").first().getAttribute("data-card-id"),
    "stage",
  );
  await card(page, "stage")
    .getByRole("button", { name: "Mută cardul la dreapta", exact: true })
    .click();
  assert.equal(
    await page.locator("[data-card-id]").first().getAttribute("data-card-id"),
    "lounge",
  );
  await page.getByRole("button", { name: "Anulează", exact: true }).click();
  await page
    .getByRole("button", { name: "Deschide Designer", exact: true })
    .waitFor();
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("layout conflict preserves draft and blocks stale save", async () => {
  const { page, context } = await fixture();
  await design(page);
  await page.evaluate(() => {
    window.revision++;
    window.events.jai_lighting_dashboard_updated({
      data: { revision: window.revision },
    });
  });
  await page.waitForFunction(() => window.panel.external);
  assert(
    await page
      .getByRole("button", { name: "Salvează design", exact: true })
      .isDisabled(),
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("disconnect disables controls and reconnect refreshes without replaying commands", async () => {
  const { page, context } = await fixture();
  await page.evaluate(() => {
    window.connection.connected = false;
    window.statusEvents.disconnected();
  });
  assert(
    await card(page, "lounge")
      .getByRole("button", { name: "Pornește Lounge", exact: true })
      .isDisabled(),
  );
  await page.evaluate(() => {
    window.connection.connected = true;
    window.statusEvents.ready();
  });
  await page.waitForFunction(
    () => window.panel.online && !window.panel.loading,
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("service errors are visible and do not retry on state updates", async () => {
  const { page, context } = await fixture();
  await page.evaluate(() => (window.failNext = true));
  await card(page, "lounge")
    .getByRole("button", { name: "Pornește Lounge", exact: true })
    .click();
  await page.getByRole("alert").waitFor();
  assert.match(
    await page.getByRole("alert").innerText(),
    /Synthetic service failure/,
  );
  await page.evaluate(() => window.refresh());
  assert.equal((await commands(page)).length, 1);
  await context.close();
});
test("read-only operator gets controls but no Designer", async () => {
  const { page, context } = await fixture();
  await page.evaluate(() => {
    window.fakeHass.user.is_admin = false;
    window.refresh();
  });
  await page.waitForFunction(
    () => !window.panel.shadowRoot.querySelector("[data-action=designer]"),
  );
  assert.equal(
    await page
      .getByRole("button", { name: "Deschide Designer", exact: true })
      .count(),
    0,
  );
  await context.close();
});
test("synthetic screenshots at iPad landscape and phone, kiosk is explicit fullscreen only", async () => {
  await mkdir("/tmp/jai-control-review", { recursive: true });
  for (const [w, h] of [
    [1024, 768],
    [390, 844],
  ]) {
    const { page, context, errors } = await fixture(w, h, true);
    assert(
      await page
        .getByRole("button", { name: "Ecran complet", exact: true })
        .isVisible(),
    );
    assert(
      (await page
        .getByRole("button", { name: "Navigare Home Assistant", exact: true })
        .count()) === 1,
    );
    await page.screenshot({
      path: `/tmp/jai-control-review/dashboard-${w}.png`,
    });
    assert.deepEqual(errors, []);
    await context.close();
  }
});

test("an open Designer form survives catalog updates without discarding unsaved input", async () => {
  const { page, context } = await fixture();
  await design(page);
  await card(page, "lounge")
    .getByRole("button", { name: "Editează", exact: true })
    .click();
  await page
    .getByLabel("Titlu opțional", { exact: true })
    .fill("Unsubmitted text");
  await page.evaluate(() =>
    window.events.entity_registry_updated({ data: { action: "update" } }),
  );
  await page.waitForFunction(
    () =>
      window.requests.filter((r) => r.command === "get").length >= 2 &&
      !window.panel.loading,
  );
  assert.equal(
    await page.getByLabel("Titlu opțional", { exact: true }).inputValue(),
    "Unsubmitted text",
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("active tab survives a reload and deleted groups become unavailable without topology edits", async () => {
  const { page, context } = await fixture();
  await page.getByRole("tab", { name: "Terasă", exact: true }).click();
  await page.reload();
  await page.getByRole("tab", { name: "Terasă", exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("tab", { name: "Terasă", exact: true })
      .getAttribute("aria-selected"),
    "true",
  );
  await page.evaluate(() => {
    delete window.catalog.groups.terrace;
    delete window.states["light.synthetic_terrace"];
    window.catalog.topology_revision++;
    window.events.jai_lighting_manager_updated({
      data: { config_revision: window.catalog.topology_revision },
    });
    window.refresh();
  });
  await page.waitForFunction(() => !window.panel.catalog.groups.terrace);
  assert.match(await card(page, "terrace").innerText(), /Indisponibil/);
  assert.deepEqual(await commands(page), []);
  await context.close();
});
test("tab rename, ordering and mood configuration are visual and non-operational", async () => {
  const { page, context } = await fixture();
  await design(page);
  await page.getByRole("button", { name: "Taburi", exact: true }).click();
  await page
    .getByRole("button", { name: "Mută Terasă înainte", exact: true })
    .click();
  assert.equal(
    await page.locator(".editor-row span").first().innerText(),
    "Terasă",
  );
  await page
    .locator(".editor-row")
    .first()
    .getByRole("button", { name: "Nume", exact: true })
    .click();
  await page
    .getByLabel("Numele tabului", { exact: true })
    .fill("Outside renamed");
  await apply(page);
  await page
    .getByRole("tab", { name: "Outside renamed", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Alege atmosfere", exact: true })
    .click();
  await page.locator("select[name=moods]").selectOption("preset:evening");
  await apply(page);
  assert.deepEqual(await commands(page), []);
  await context.close();
});

test("a partial event subscription failure cleans up and recovers without commands", async () => {
  const { page, context } = await fixture();
  const result = await page.evaluate(async () => {
    const panel = window.panel;
    for (const off of panel.unsubs.splice(0)) off();
    const original = window.connection.subscribeEvents;
    let attempts = 0;
    window.connection.subscribeEvents = async (...args) => {
      if (++attempts === 3) throw new Error("Synthetic subscription failure");
      return original(...args);
    };
    await panel.load();
    const failed = {
      error: panel.error,
      listeners: Object.keys(window.events).length,
      subscribing: panel.subscribing,
    };
    window.connection.subscribeEvents = original;
    await panel.load();
    return {
      failed,
      listeners: Object.keys(window.events).length,
      error: panel.error,
    };
  });
  assert.match(result.failed.error, /Synthetic subscription failure/);
  assert.equal(result.failed.listeners, 0);
  assert.equal(result.failed.subscribing, false);
  assert.equal(result.listeners, 5);
  assert.equal(result.error, "");
  await design(page);
  assert.equal(
    await page.locator('[data-action="details"]:enabled').count(),
    0,
  );
  assert.deepEqual(await commands(page), []);
  await context.close();
});
