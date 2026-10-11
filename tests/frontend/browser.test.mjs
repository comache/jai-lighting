import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { fixtureHtml } from "./fixture.mjs";
import { chromium } from "playwright";
let browser, server, url;
before(async () => {
  server = createServer(async (req, res) => {
    if (req.url === "/") {
      res.setHeader("Content-Type", "text/html");
      res.end(fixtureHtml());
      return;
    }
    if (
      ![
        "/manager.js",
        "/model.js",
        "/styles.js",
        "/dom.js",
        "/group-picker.js",
      ].includes(req.url)
    ) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.setHeader("Content-Type", "text/javascript");
    res.end(
      await readFile(
        new URL(
          `../../custom_components/jai_lighting/frontend${req.url}`,
          import.meta.url,
        ),
      ),
    );
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  url = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({
    headless: true,
    ...(process.env.JAI_BROWSER_CHANNEL
      ? { channel: process.env.JAI_BROWSER_CHANNEL }
      : {}),
  });
});
after(async () => {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
});
async function pageFor(width = 1440, touch = false) {
  const context = await browser.newContext({
    viewport: { width, height: width < 761 ? 844 : 1000 },
    hasTouch: touch,
  });
  const page = await context.newPage(),
    errors = [];
  page.setDefaultTimeout(7000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  await page.getByRole("tree", { name: "Lighting hierarchy" }).waitFor();
  return { page, context, errors };
}
const row = (page, key) => page.locator(`[data-key="${key}"]`);
const log = (page) => page.evaluate(() => window.requests);
async function saved(page, revision = 1) {
  await page.waitForFunction(
    (n) => window.sample.config_revision === n && !window.panel.busy,
    revision,
  );
}
async function safe(page, errors) {
  assert(
    !(await log(page)).some(
      (r) => r.domain || ["control", "activate"].includes(r.command),
    ),
  );
  assert.deepEqual(errors, []);
}

test("first root creation retains native validation and issues no lighting calls", async () => {
  const { page, context, errors } = await pageFor();
  await page.getByRole("button", { name: "Create group", exact: true }).click();
  await page.getByRole("textbox", { name: "Group name" }).fill("New Root");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review new group" }).waitFor();
  await page.getByRole("button", { name: "Continue / save" }).click();
  await saved(page);
  const create = (await log(page)).find((r) => r.command === "create");
  assert.equal(create.data.parent, "");
  assert.deepEqual(create.data.children, []);
  assert.equal(create.data.allow_moves, false);
  assert.equal(create.data.order, 0);
  assert((await log(page)).some((r) => r.command === "save_creation"));
  await safe(page, errors);
  await context.close();
});

test("chevrons are separate from selection and expansion survives edits, tabs, refresh and reload", async () => {
  const { page, context, errors } = await pageFor();
  await row(page, "group:root").click();
  await page
    .getByRole("button", { name: "Collapse Root", exact: true })
    .click();
  assert.equal(
    await row(page, "group:root").getAttribute("aria-selected"),
    "true",
  );
  assert.equal(await row(page, "group:child").count(), 0);
  await page.getByRole("button", { name: "Collections", exact: true }).click();
  await page.getByRole("button", { name: "Hierarchy", exact: true }).click();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.reload();
  await page.getByRole("tree").waitFor();
  assert.equal(await row(page, "group:child").count(), 0);
  await page.getByRole("button", { name: "Expand Root", exact: true }).click();
  await row(page, "leaf:one").click();
  await page
    .getByRole("button", { name: "Remove from hierarchy", exact: true })
    .click();
  await saved(page);
  assert.equal(
    await row(page, "group:root").getAttribute("aria-expanded"),
    "true",
  );
  await page.getByRole("button", { name: "Collapse all", exact: true }).click();
  assert.equal(await page.getByRole("treeitem").count(), 2);
  await page.getByRole("button", { name: "Expand all", exact: true }).click();
  assert.equal(await page.getByRole("treeitem").count(), 4);
  await safe(page, errors);
  await context.close();
});

test("collapsed rows retain actual direct, descendant and unavailable counts", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    sample.inventory.lights[0].state = "unavailable";
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page
    .getByRole("button", { name: "Collapse Root", exact: true })
    .click();
  const text = await row(page, "group:root").innerText();
  assert.match(text, /1 groups · 0 direct · 1 total lights/);
  assert.match(text, /1 unavailable/);
  await safe(page, errors);
  await context.close();
});

test("single, modifier, range, empty-space and keyboard selection are file-manager interactions", async () => {
  const { page, context, errors } = await pageFor();
  await row(page, "group:root").click();
  assert.equal(await page.locator('[aria-selected="true"]').count(), 1);
  await row(page, "group:other").click({ modifiers: ["Control"] });
  assert.equal(await page.locator('[aria-selected="true"]').count(), 2);
  await row(page, "group:root").click();
  await row(page, "leaf:one").click({ modifiers: ["Shift"] });
  assert.equal(await page.locator('[aria-selected="true"]').count(), 3);
  await page.locator(".root").click();
  assert.equal(await page.locator('[aria-selected="true"]').count(), 0);
  await row(page, "group:root").click();
  await row(page, "group:root").press("ArrowDown");
  assert.equal(
    await row(page, "group:child").getAttribute("aria-selected"),
    "true",
  );
  await row(page, "group:child").press("ArrowLeft");
  assert.equal(
    await row(page, "group:child").getAttribute("aria-expanded"),
    "false",
  );
  await row(page, "group:child").press("ArrowRight");
  assert.equal(
    await row(page, "group:child").getAttribute("aria-expanded"),
    "true",
  );
  await safe(page, errors);
  await context.close();
});

test("contextual actions explain disabled states and inspector add button shows eligible count", async () => {
  const { page, context, errors } = await pageFor();
  await page.getByLabel("More actions", { exact: true }).click();
  assert(
    await page
      .getByRole("button", { name: "Rename group", exact: true })
      .isDisabled(),
  );
  assert.match(
    await page
      .getByRole("button", { name: "Move up", exact: true })
      .getAttribute("title"),
    /previous sibling/,
  );
  await page.getByLabel("More actions", { exact: true }).click();
  await row(page, "group:root").click();
  assert(
    await page
      .getByRole("button", { name: "Add 0 lights", exact: true })
      .isDisabled(),
  );
  await page.locator('[data-light="light.three"]').check();
  assert.match(
    await page.locator(".light-browser").innerText(),
    /Add lights to: Root/i,
  );
  assert(
    await page
      .getByRole("button", { name: "Add 1 light", exact: true })
      .isEnabled(),
  );
  assert(
    await page
      .getByRole("button", { name: "Edit name / policy", exact: true })
      .isEnabled(),
  );
  await safe(page, errors);
  await context.close();
});

test("Hierarchy browser excludes assigned lights with labels; Collections allows them and individual exceptions", async () => {
  const { page, context, errors } = await pageFor();
  assert.equal(await page.locator("[data-light]").count(), 2);
  await page.getByText("Filter lights", { exact: true }).click();
  await page.getByLabel("HA label", { exact: true }).selectOption("test");
  assert.equal(await page.locator("[data-light]").count(), 1);
  assert.equal(await page.locator('[data-light="light.one"]').count(), 0);
  await page.getByRole("button", { name: "Collections", exact: true }).click();
  assert.equal(await page.locator("[data-light]").count(), 2);
  await page
    .getByRole("button", { name: "Select all matching", exact: true })
    .click();
  await page.locator('[data-light="light.three"]').uncheck();
  await page
    .getByRole("button", { name: "Create collection", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Test Collection");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review changes" }).waitFor();
  const preview = (await log(page)).find((r) => r.command === "preview");
  assert.deepEqual(preview.operations[0].add_entity_ids, ["light.one"]);
  assert(!("filters" in preview.operations[0]));
  await page.getByRole("button", { name: "Continue / save" }).click();
  await saved(page);
  assert.equal(
    await page.evaluate(() => sample.leaves.one.parent_group_id),
    "child",
  );
  await safe(page, errors);
  await context.close();
});

test("add/remove auto-save durably, update browser/counts and support revision-safe Undo", async () => {
  const { page, context, errors } = await pageFor();
  await row(page, "group:child").click();
  await page.locator('[data-light="light.three"]').check();
  await page.getByRole("button", { name: "Add 1 light", exact: true }).click();
  await saved(page);
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.locator('[data-light="light.three"]').count(), 0);
  assert.match(
    await row(page, "group:child").innerText(),
    /2 direct · 2 total lights/,
  );
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await saved(page, 2);
  assert.equal(await page.locator('[data-light="light.three"]').count(), 1);
  assert.match(
    await row(page, "group:child").innerText(),
    /1 direct · 1 total lights/,
  );
  await row(page, "leaf:one").click();
  await page
    .getByRole("button", { name: "Remove from hierarchy", exact: true })
    .click();
  await saved(page, 3);
  assert.equal(await row(page, "leaf:one").count(), 0);
  assert.equal(await page.locator('[data-light="light.one"]').count(), 1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await saved(page, 4);
  assert.equal(await page.locator('[data-light="light.one"]').count(), 0);
  assert.equal(await row(page, "leaf:one").count(), 1);
  await safe(page, errors);
  await context.close();
});

test("failed durable save rolls back optimistic tree and reloads the authoritative state", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    window.failNextApply = true;
  });
  await row(page, "leaf:one").click();
  await page
    .getByRole("button", { name: "Remove from hierarchy", exact: true })
    .click();
  await page.getByRole("alert").filter({ hasText: "Disk failure" }).waitFor();
  assert.equal(await row(page, "leaf:one").count(), 1);
  assert.equal(await page.locator('[data-light="light.one"]').count(), 0);
  assert.equal(await page.evaluate(() => sample.config_revision), 0);
  await safe(page, errors);
  await context.close();
});

test("concurrent changes between preview and save are retained and never silently overwritten", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    window.concurrentBeforeApply = true;
  });
  await row(page, "leaf:one").click();
  await page
    .getByRole("button", { name: "Remove from hierarchy", exact: true })
    .click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Configuration changed" })
    .waitFor();
  assert.match(
    await row(page, "group:other").innerText(),
    /Other administrator/,
  );
  assert.equal(await row(page, "leaf:one").count(), 1);
  await safe(page, errors);
  await context.close();
});

test("Undo refuses a newer administrator revision", async () => {
  const { page, context, errors } = await pageFor();
  await row(page, "leaf:one").click();
  await page
    .getByRole("button", { name: "Remove from hierarchy", exact: true })
    .click();
  await saved(page);
  await page.evaluate(() => {
    sample.config_revision++;
    sample.groups.other.name = "Newer edit";
  });
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Configuration changed" })
    .waitFor();
  assert.equal(
    await page.evaluate(() => sample.groups.other.name),
    "Newer edit",
  );
  assert.equal(
    await page.evaluate(() => sample.leaves.one.parent_group_id),
    null,
  );
  await safe(page, errors);
  await context.close();
});

test("rapid edits are serialized against the latest confirmed revision", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(async () => {
    window.saveDelay = 35;
    await Promise.all([
      panel.saveTopology(
        [
          {
            action: "move",
            items: [{ kind: "leaf", id: "one" }],
            parent: "root",
          },
        ],
        "First edit",
      ),
      panel.saveTopology(
        [
          {
            action: "move",
            items: [{ kind: "leaf", id: "two" }],
            parent: "child",
          },
        ],
        "Second edit",
      ),
    ]);
  });
  const applies = (await log(page)).filter((r) => r.command === "apply");
  assert.deepEqual(
    applies.map((r) => r.revision),
    [0, 1],
  );
  assert.equal(
    await page.evaluate(() => sample.leaves.one.parent_group_id),
    "root",
  );
  assert.equal(
    await page.evaluate(() => sample.leaves.two.parent_group_id),
    "child",
  );
  await safe(page, errors);
  await context.close();
});

test("desktop drag moves a light, reorders groups and rejects cycles visually", async () => {
  const { page, context, errors } = await pageFor();
  const target = row(page, "group:other"),
    box = await target.boundingBox();
  await page
    .locator('[data-drag="leaf:one"]')
    .first()
    .dragTo(target, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
  await saved(page);
  assert.equal(
    await page.evaluate(() => sample.leaves.one.parent_group_id),
    "other",
  );
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await saved(page, 2);
  await page
    .locator('[data-drag="group:other"]')
    .first()
    .dragTo(row(page, "group:root"), { targetPosition: { x: 150, y: 4 } });
  await saved(page, 3);
  assert.deepEqual(
    await page.evaluate(() => [
      sample.groups.other.sibling_order,
      sample.groups.root.sibling_order,
    ]),
    [0, 1],
  );
  // Real drag hover inside a descendant must be visibly rejected before drop.
  const source = await page
      .locator('button[data-drag="group:root"]')
      .boundingBox(),
    child = await row(page, "group:child").boundingBox();
  await page.mouse.move(source.x + 10, source.y + 15);
  await page.mouse.down();
  await page.mouse.move(source.x + 20, source.y + 15, { steps: 5 });
  await page.mouse.move(child.x + 120, child.y + child.height / 2, {
    steps: 6,
  });
  assert.equal(
    await row(page, "group:child").getAttribute("data-drop-state"),
    "invalid",
  );
  await page.mouse.up();
  assert.equal(await page.evaluate(() => sample.config_revision), 3);
  await safe(page, errors);
  await context.close();
});

test("browser drag assigns an unavailable unassigned light without commands", async () => {
  const { page, context, errors } = await pageFor();
  const target = row(page, "group:child"),
    box = await target.boundingBox();
  await page
    .locator('[data-drag="inventory:light.three"]')
    .first()
    .dragTo(target, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
  await saved(page);
  assert.equal(await page.locator('[data-light="light.three"]').count(), 0);
  assert.match(await row(page, "group:child").innerText(), /1 unavailable/);
  await safe(page, errors);
  await context.close();
});

for (const width of [390, 820, 1440])
  test(`responsive ${width}px, theme tokens and inspector retain OFF-safe manual fader`, async () => {
    const { page, context, errors } = await pageFor(width, width < 1000);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await row(page, "group:root").click();
    if (width < 761)
      await page
        .getByRole("button", { name: "Available Lights", exact: true })
        .click();
    await page.getByText("Lighting controls", { exact: true }).click();
    await page
      .getByRole("slider", { name: "Root relative master", exact: true })
      .fill("50");
    await page
      .getByRole("slider", { name: "Root relative master", exact: true })
      .dispatchEvent("change");
    await page.waitForFunction(() =>
      requests.some((r) => r.service === "set_master"),
    );
    const fader = (await log(page)).find((r) => r.service === "set_master");
    assert.equal(fader.domain, "jai_lighting");
    assert.equal(fader.data.brightness_pct, 50);
    assert(!(await log(page)).some((r) => r.domain === "light"));
    assert.deepEqual(errors, []);
    await context.close();
  });

test("touch drag and Move to alternatives persist equivalent moves on phone", async () => {
  const { page, context, errors } = await pageFor(390, true);
  const handle = page.locator('button[data-drag="leaf:one"]'),
    target = row(page, "group:other");
  await target.scrollIntoViewIfNeeded();
  const start = await handle.boundingBox(),
    end = await target.boundingBox();
  const session = await context.newCDPSession(page),
    point = (x, y) => [{ x, y, id: 1 }];
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: point(start.x + 10, start.y + 15),
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: point(end.x + 120, end.y + end.height / 2),
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await saved(page);
  const drag = (await log(page)).find((r) => r.command === "apply").operations;
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await saved(page, 2);
  await row(page, "leaf:one").click();
  await page.getByRole("button", { name: "Move to…", exact: true }).click();
  await page.getByRole("button", { name: "Destination", exact: true }).click();
  await page
    .locator("jai-group-picker")
    .getByRole("treeitem", { name: "Other", exact: true })
    .click();
  await page.getByRole("button", { name: "Continue / save" }).click();
  await saved(page, 3);
  const applies = (await log(page)).filter((r) => r.command === "apply");
  assert.deepEqual(applies.at(-1).operations, drag);
  await safe(page, errors);
  await context.close();
});

test("preset editor preserves sparse collection assignments and never activates during editing", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    sample.collections = { c: { name: "Test Collection", members: ["one"] } };
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("button", { name: "Presets", exact: true }).click();
  await page
    .getByRole("button", { name: "Create preset", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Test Recipe");
  await page.getByRole("button", { name: "Add assignment" }).click();
  await page.getByRole("button", { name: "Target", exact: true }).click();
  await page
    .getByRole("treeitem", {
      name: "collection · Test Collection",
      exact: true,
    })
    .click();
  await page.getByLabel("Assign brightness (%)", { exact: true }).check();
  await page
    .getByRole("spinbutton", { name: "Brightness (%)", exact: true })
    .fill("35");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review new preset" }).waitFor();
  const create = (await log(page)).find((r) => r.command === "create");
  assert.deepEqual(create.data.targets, [
    {
      kind: "collection",
      id: "c",
      properties: { power: "on", brightness_pct: 35 },
    },
  ]);
  await safe(page, errors);
  await context.close();
});

test("200-light installation supports search, persistent counts and no horizontal overflow", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    for (let i = 0; i < 200; i++) {
      sample.leaves[`large-${i}`] = {
        parent_group_id: "child",
        sibling_order: i + 1,
      };
      sample.routes[`large-${i}`] = `light.fixture_${i}`;
      sample.inventory.lights.push({
        entity_id: `light.fixture_${i}`,
        name: `Fixture light ${i}`,
        state: i % 4 ? "off" : "unavailable",
        labels: [],
        area_id: null,
        device_id: null,
      });
    }
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page
    .getByRole("button", { name: "Collapse Child", exact: true })
    .click();
  assert.match(await row(page, "group:root").innerText(), /201 total lights/);
  await page
    .getByRole("textbox", { name: "Search hierarchy", exact: true })
    .fill("light.fixture_199");
  assert.equal(await row(page, "leaf:large-199").count(), 1);
  assert.equal(await page.getByRole("treeitem").count(), 3);
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  await safe(page, errors);
  await context.close();
});

test("dark/light HA themes and responsive development screenshots", async () => {
  const directory = process.env.JAI_SCREENSHOT_DIR;
  for (const theme of ["light", "dark"])
    for (const width of [390, 820, 1440]) {
      const { page, context, errors } = await pageFor(width, width < 1000);
      await page.evaluate((theme) => {
        const vars =
          theme === "dark"
            ? {
                "--primary-background-color": "#15191e",
                "--card-background-color": "#20262e",
                "--secondary-background-color": "#2b333e",
                "--primary-text-color": "#ebeff5",
                "--secondary-text-color": "#9aa8b9",
                "--divider-color": "#343d48",
                "--primary-color": "#69bfea",
              }
            : {
                "--primary-background-color": "#f5f7fa",
                "--card-background-color": "#ffffff",
                "--primary-text-color": "#20252d",
                "--primary-color": "#007aab",
              };
        Object.entries(vars).forEach(([key, value]) =>
          document.body.style.setProperty(key, value),
        );
      }, theme);
      await row(page, "group:child").click();
      const color = await page
        .locator("jai-lighting-manager")
        .evaluate((el) => getComputedStyle(el).color);
      assert.equal(
        color,
        theme === "dark" ? "rgb(235, 239, 245)" : "rgb(32, 37, 45)",
      );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      if (directory) {
        await mkdir(directory, { recursive: true });
        await page.screenshot({
          path: `${directory}/manager-${theme}-${width}.png`,
          fullPage: true,
        });
        if (width < 761) {
          await page
            .getByRole("button", { name: "Available Lights", exact: true })
            .click();
          await page.screenshot({
            path: `${directory}/manager-${theme}-${width}-lights.png`,
          });
        }
      }
      await safe(page, errors);
      await context.close();
    }
});

async function large(page) {
  const { largeSample } = await import("./fixture.mjs");
  await page.evaluate((data) => {
    window.sample = data;
  }, largeSample());
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await row(page, "group:g-199").waitFor();
  await page.getByRole("button", { name: "Expand all", exact: true }).click();
}

test("hierarchical picker preserves order, expansion, search ancestors, disabled subtrees and keyboard choice", async () => {
  const { page, context, errors } = await pageFor();
  await row(page, "group:root").click();
  await page.getByRole("button", { name: "Move to…", exact: true }).click();
  await page.getByRole("button", { name: "Destination", exact: true }).click();
  const picker = page.locator("jai-group-picker");
  assert.equal(
    await page
      .getByRole("button", { name: "Destination", exact: true })
      .getAttribute("aria-expanded"),
    "true",
  );
  if (process.env.JAI_SCREENSHOT_DIR)
    await page.screenshot({
      path: `${process.env.JAI_SCREENSHOT_DIR}/picker-desktop.png`,
    });
  assert.deepEqual(await picker.getByRole("treeitem").allTextContents(), [
    "○ Root / Unassigned",
    "Root",
    "Child",
    "Other",
  ]);
  assert(
    await picker
      .getByRole("treeitem", { name: "Root", exact: true })
      .isDisabled(),
  );
  assert(
    await picker
      .getByRole("treeitem", { name: "Child", exact: true })
      .isDisabled(),
  );
  await picker
    .getByRole("button", { name: "Collapse Root", exact: true })
    .click();
  assert.equal(
    await picker.getByRole("treeitem", { name: "Child", exact: true }).count(),
    0,
  );
  await picker.getByRole("textbox", { name: "Search groups" }).fill("Child");
  assert.equal(
    await picker.getByRole("treeitem", { name: "Root", exact: true }).count(),
    1,
  );
  assert.equal(
    await picker.getByRole("treeitem", { name: "Child", exact: true }).count(),
    1,
  );
  await picker.getByRole("textbox", { name: "Search groups" }).fill("");
  await picker.getByRole("textbox", { name: "Search groups" }).press("End");
  await picker
    .getByRole("treeitem", { name: "Other", exact: true })
    .press("Enter");
  assert.match(
    await page
      .getByRole("button", { name: "Destination", exact: true })
      .innerText(),
    /Other/,
  );
  await page.getByRole("button", { name: "Continue / save" }).click();
  await saved(page);
  assert.equal(
    await page.evaluate(() => sample.groups.root.parent_group_id),
    "other",
  );
  await safe(page, errors);
  await context.close();
});

for (const width of [390, 820, 1440])
  test(`picker popup remains inside ${width}px viewport and selects no parent`, async () => {
    const { page, context, errors } = await pageFor(width, width < 1000);
    await page
      .getByRole("button", { name: "Create group", exact: true })
      .click();
    await page.getByRole("button", { name: "Parent", exact: true }).click();
    const popup = await page
      .locator("jai-group-picker")
      .locator("[popover]")
      .boundingBox();
    assert(
      popup.x >= 0 &&
        popup.y >= 0 &&
        popup.x + popup.width <= width &&
        popup.y + popup.height <= (width < 761 ? 844 : 1000),
    );
    await page
      .locator("jai-group-picker")
      .getByRole("treeitem", { name: "○ Root / Unassigned", exact: true })
      .click();
    assert.equal(
      await page.locator("jai-group-picker").evaluate((el) => el.value),
      "",
    );
    await safe(page, errors);
    await context.close();
  });

for (const width of [390, 820, 1440])
  test(`independent ${width}px panels retain controls, scroll and selection when switching`, async () => {
    const { page, context, errors } = await pageFor(width, width < 1000);
    await large(page);
    await row(page, "group:g-0").click();
    const before = await page.evaluate(() => {
      const s = panel.shadowRoot;
      s.querySelector(".tree").scrollTop = 950;
      return {
        header: s.querySelector("header").getBoundingClientRect().top,
        search: s.querySelector('[data-search="tree"]').getBoundingClientRect()
          .top,
      };
    });
    if (width < 761)
      await page
        .getByRole("button", { name: "Available Lights", exact: true })
        .click();
    await page.locator('[data-light="light.synthetic_150"]').check();
    await page.evaluate(
      () =>
        (panel.shadowRoot.querySelector(
          ".light-browser .member-list",
        ).scrollTop = 450),
    );
    const scroll = await page.evaluate(() => ({
      tree: panel.shadowRoot.querySelector(".tree").scrollTop,
      browser: panel.shadowRoot.querySelector(".light-browser .member-list")
        .scrollTop,
    }));
    if (width < 761)
      await page
        .locator('[data-action="panel"][data-panel="hierarchy"]')
        .click();
    assert.equal(
      await row(page, "group:g-0").getAttribute("aria-selected"),
      "true",
    );
    assert.equal(
      await page.evaluate(
        () => panel.shadowRoot.querySelector(".tree").scrollTop,
      ),
      950,
    );
    assert.deepEqual(
      await page.evaluate(() => ({
        header: panel.shadowRoot.querySelector("header").getBoundingClientRect()
          .top,
        search: panel.shadowRoot
          .querySelector('[data-search="tree"]')
          .getBoundingClientRect().top,
      })),
      before,
    );
    if (width < 761)
      await page
        .getByRole("button", { name: "Available Lights", exact: true })
        .click();
    assert.equal(
      await page.evaluate(
        () =>
          panel.shadowRoot.querySelector(".light-browser .member-list")
            .scrollTop,
      ),
      scroll.browser,
    );
    assert.equal(
      await page.locator('[data-light="light.synthetic_150"]').isChecked(),
      true,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollHeight > innerHeight,
      ),
      false,
    );
    assert(
      await page
        .getByRole("textbox", { name: "Search physical lights" })
        .isVisible(),
    );
    await safe(page, errors);
    await context.close();
  });

test("keyed reactive edits preserve row/container identity, anchors, partial browser selection, filters and inspector", async () => {
  const { page, context, errors } = await pageFor();
  await large(page);
  await row(page, "group:g-0").click();
  await page
    .getByRole("textbox", { name: "Search physical lights" })
    .fill("synthetic");
  await page.getByText("Filter lights", { exact: true }).click();
  await page.getByLabel("HA label", { exact: true }).selectOption("test");
  await page.getByLabel("Area", { exact: true }).selectOption("area");
  await page.getByLabel("Device", { exact: true }).selectOption("device");
  await page.evaluate(() => {
    panel.lightSelection = new Set(
      Array.from({ length: 8 }, (_, i) => `light.synthetic_${150 + i}`),
    );
    panel.render();
    const s = panel.shadowRoot;
    window.treeNode = s.querySelector(".tree");
    window.browserNode = s.querySelector(".light-browser .member-list");
    window.rowNode = s.querySelector('[data-key="group:g-100"]');
    treeNode.scrollTop = 950;
    browserNode.scrollTop = 450;
    window.anchor = [...s.querySelectorAll(".tree-row")].find(
      (row) =>
        row.getBoundingClientRect().bottom >
        treeNode.getBoundingClientRect().top,
    );
    window.anchorOffset = anchor.getBoundingClientRect().top;
  });
  let navigations = 0;
  page.on("framenavigated", () => navigations++);
  await page.evaluate(() =>
    panel.saveTopology(
      [
        {
          action: "add_lights",
          parent: "g-0",
          entity_ids: [
            "light.synthetic_150",
            "light.synthetic_151",
            "light.synthetic_152",
          ],
        },
      ],
      "Added three lights",
    ),
  );
  await saved(page);
  const result = await page.evaluate(() => ({
    treeSame: treeNode === panel.shadowRoot.querySelector(".tree"),
    browserSame:
      browserNode ===
      panel.shadowRoot.querySelector(".light-browser .member-list"),
    rowSame:
      rowNode === panel.shadowRoot.querySelector('[data-key="group:g-100"]'),
    anchorMoved: Math.abs(anchor.getBoundingClientRect().top - anchorOffset),
    selected: [...panel.lightSelection],
    group: [...panel.selection],
    filters: panel.filters,
    expanded: panel.expanded.has("g-0"),
    tab: panel.tab,
  }));
  assert(result.treeSame && result.browserSame && result.rowSame);
  assert(result.anchorMoved <= 1);
  assert.deepEqual(
    result.selected,
    Array.from({ length: 5 }, (_, i) => `light.synthetic_${153 + i}`),
  );
  assert.deepEqual(result.group, ["group:g-0"]);
  assert.equal(result.filters.query, "synthetic");
  assert.equal(result.filters.label, "test");
  assert(result.expanded);
  assert.equal(result.tab, "Hierarchy");
  assert.equal(navigations, 0);
  assert.match(
    await page.locator(".inspector").innerText(),
    /Direct lights\n4/,
  );
  assert.equal(
    await page.locator('[data-light="light.synthetic_150"]').count(),
    0,
  );
  assert.equal(
    await page
      .getByRole("textbox", { name: "Search physical lights" })
      .inputValue(),
    "synthetic",
  );
  await safe(page, errors);
  await context.close();
});

test("failed save preserves context; catalog scroll survives a tab round trip", async () => {
  const { page, context, errors } = await pageFor();
  await large(page);
  await row(page, "group:g-0").click();
  await page.getByRole("button", { name: "Collections", exact: true }).click();
  await row(page, "collection:c-2").click();
  await page.evaluate(
    () => (panel.shadowRoot.querySelector(".catalog").scrollTop = 900),
  );
  await page.getByRole("button", { name: "Hierarchy", exact: true }).click();
  await page.evaluate(() => {
    panel.shadowRoot.querySelector(".tree").scrollTop = 1200;
    window.failNextApply = true;
  });
  await page.evaluate(() =>
    panel.saveTopology(
      [
        {
          action: "move",
          items: [{ kind: "leaf", id: "l-0" }],
          parent: "g-199",
        },
      ],
      "Move",
    ),
  );
  assert.match(await page.getByRole("alert").innerText(), /Disk failure/);
  assert.deepEqual(await page.evaluate(() => [...panel.selection]), [
    "group:g-0",
  ]);
  assert.equal(
    await page.evaluate(
      () => panel.shadowRoot.querySelector(".tree").scrollTop,
    ),
    1200,
  );
  await page.getByRole("button", { name: "Collections", exact: true }).click();
  assert.equal(
    await page.evaluate(
      () => panel.shadowRoot.querySelector(".catalog").scrollTop,
    ),
    900,
  );
  assert.equal(
    await row(page, "collection:c-2").getAttribute("aria-selected"),
    "true",
  );
  await safe(page, errors);
  await context.close();
});

test("200 groups, 300 lights and a 20-deep branch keep keyed interactions responsive", async () => {
  const { page, context, errors } = await pageFor();
  await large(page);
  assert.equal(await page.getByRole("treeitem").count(), 350);
  const result = await page.evaluate(() => {
    const started = performance.now();
    panel.selection = new Set(["group:g-0"]);
    panel.search = "Group 019";
    panel.render();
    const searchMs = performance.now() - started,
      ancestors = panel.shadowRoot.querySelectorAll(".tree-row").length;
    panel.search = "";
    const before = performance.now();
    panel.render();
    return { searchMs, renderMs: performance.now() - before, ancestors };
  });
  assert.equal(result.ancestors, 20);
  assert(result.searchMs < 500, `search: ${result.searchMs}ms`);
  assert(result.renderMs < 500, `render: ${result.renderMs}ms`);
  console.log(
    `Large fixture: search ${result.searchMs.toFixed(1)} ms; keyed render ${result.renderMs.toFixed(1)} ms`,
  );
  await safe(page, errors);
  await context.close();
});

test("modifier browser drag retains unrelated selected lights and panel-only edge autoscroll", async () => {
  const { page, context, errors } = await pageFor();
  await large(page);
  await row(page, "group:g-0").click();
  await page.evaluate(() => {
    panel.lightSelection = new Set([
      "light.synthetic_150",
      "light.synthetic_151",
    ]);
    panel.render();
  });
  const target = row(page, "group:g-0"),
    box = await target.boundingBox();
  await page
    .locator('[data-drag="inventory:light.synthetic_152"]')
    .first()
    .dragTo(target, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
  await saved(page);
  assert.deepEqual(await page.evaluate(() => [...panel.lightSelection]), [
    "light.synthetic_150",
    "light.synthetic_151",
  ]);
  assert.equal(
    await row(page, "group:g-0").getAttribute("aria-selected"),
    "true",
  );
  const scrolled = await page.evaluate(async () => {
    const tree = panel.shadowRoot.querySelector(".tree"),
      browser = panel.shadowRoot.querySelector(".light-browser .member-list"),
      rect = tree.getBoundingClientRect(),
      initial = browser.scrollTop;
    panel.dragging = [{ kind: "group", id: "g-199" }];
    panel.autoScroll(rect.x + 100, rect.bottom - 5);
    await new Promise((r) => setTimeout(r, 150));
    panel.dragging = null;
    panel.clearDrop();
    return {
      tree: tree.scrollTop,
      browser: browser.scrollTop,
      initial,
      page: window.scrollY,
    };
  });
  assert(scrolled.tree > 0);
  assert.equal(scrolled.browser, scrolled.initial);
  assert.equal(scrolled.page, 0);
  await safe(page, errors);
  await context.close();
});

test("HA manager events update external edits while preserving context and defer safely during dialogs", async () => {
  const { page, context, errors } = await pageFor();
  await large(page);
  await row(page, "group:g-0").click();
  await page.evaluate(() => {
    panel.shadowRoot.querySelector(".tree").scrollTop = 1000;
    sample.groups["g-100"].name = "External name";
    sample.config_revision++;
    managerEvent();
  });
  await page.waitForFunction(() => panel.data.config_revision === 1);
  assert.equal(
    await page.evaluate(() => eventType),
    "jai_lighting_manager_updated",
  );
  assert.match(await row(page, "group:g-100").innerText(), /External name/);
  assert.equal(
    await page.evaluate(
      () => panel.shadowRoot.querySelector(".tree").scrollTop,
    ),
    1000,
  );
  assert.deepEqual(await page.evaluate(() => [...panel.selection]), [
    "group:g-0",
  ]);
  await page
    .locator(".inspector")
    .getByRole("button", { name: "Edit name / policy" })
    .click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Local name");
  await page.evaluate(() => {
    sample.groups["g-0"].name = "Another administrator";
    sample.config_revision++;
    managerEvent();
  });
  await page.waitForFunction(() => panel.externalRefresh);
  assert.equal(
    await page.getByRole("textbox", { name: "Name", exact: true }).inputValue(),
    "Local name",
  );
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.waitForFunction(
    () => panel.data.config_revision === 2 && !panel.busy,
  );
  assert.match(
    await page.getByRole("alert").innerText(),
    /Configuration changed/,
  );
  assert.equal(
    await page.evaluate(() => sample.groups["g-0"].name),
    "Another administrator",
  );
  await safe(page, errors);
  await context.close();
});

test("unassigned physical availability follows HA states without polling or topology writes", async () => {
  const { page, context, errors } = await pageFor();
  const before = (await log(page)).length;
  await page.evaluate(() => {
    panel.hass = {
      ...panel.hass,
      states: Object.fromEntries(
        sample.inventory.lights.map((light) => [
          light.entity_id,
          { state: "on" },
        ]),
      ),
    };
  });
  assert.match(
    await page
      .locator('[data-drag="inventory:light.three"]')
      .first()
      .innerText(),
    /on/,
  );
  assert.match(await row(page, "group:root").innerText(), /On/);
  assert.equal((await log(page)).length, before);
  await safe(page, errors);
  await context.close();
});

test("phone touch drag can switch panels on hover and retains unrelated browser selection", async () => {
  const { page, context, errors } = await pageFor(390, true);
  await row(page, "group:child").click();
  await page
    .getByRole("button", { name: "Available Lights", exact: true })
    .click();
  await page.getByText("Filter lights", { exact: true }).click();
  await page.getByLabel("HA label", { exact: true }).selectOption("test");
  await page.getByText("Filter lights", { exact: true }).click();
  await page
    .getByRole("textbox", { name: "Search physical lights" })
    .fill("light");
  assert.equal(
    await page.locator(".filter-details").evaluate((el) => el.open),
    false,
  );
  await page.evaluate(() => {
    panel.filters = {};
    panel.render();
  });
  await page.locator('[data-light="light.four"]').check();
  await page
    .locator('button[data-drag="inventory:light.three"]')
    .scrollIntoViewIfNeeded();
  const start = await page
    .locator('button[data-drag="inventory:light.three"]')
    .boundingBox();
  const switcher = await page
    .locator('[data-action="panel"][data-panel="hierarchy"]')
    .boundingBox();
  const cdp = await context.newCDPSession(page),
    points = (x, y) => [{ x, y, id: 1 }];
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: points(start.x + 10, start.y + 15),
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: points(
      switcher.x + switcher.width / 2,
      switcher.y + switcher.height / 2,
    ),
  });
  await page.waitForFunction(() => panel.panelMode === "hierarchy");
  const end = await row(page, "group:child").boundingBox();
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: points(end.x + 100, end.y + end.height / 2),
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await saved(page);
  assert.equal(
    await page.evaluate(
      () => sample.leaves["registered-light.three"].parent_group_id,
    ),
    "child",
  );
  assert.deepEqual(await page.evaluate(() => [...panel.lightSelection]), [
    "light.four",
  ]);
  assert.equal(
    await row(page, "group:child").getAttribute("aria-selected"),
    "true",
  );
  await safe(page, errors);
  await context.close();
});
