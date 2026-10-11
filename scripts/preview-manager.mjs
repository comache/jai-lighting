// Loopback-only, synthetic browser preview. Never connects to Home Assistant.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fixtureHtml, largeSample } from "../tests/frontend/fixture.mjs";
const modules = new Set([
  "manager.js",
  "model.js",
  "styles.js",
  "dom.js",
  "group-picker.js",
]);
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end(
      fixtureHtml({
        data: url.searchParams.has("large") ? largeSample() : undefined,
        theme: url.searchParams.get("theme"),
      }),
    );
    return;
  }
  const name = url.pathname.slice(1);
  if (!modules.has(name)) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.setHeader("Content-Type", "text/javascript");
  res.end(
    await readFile(
      new URL(
        `../custom_components/jai_lighting/frontend/${name}`,
        import.meta.url,
      ),
    ),
  );
});
server.listen(Number(process.env.JAI_PREVIEW_PORT) || 0, "127.0.0.1", () =>
  console.log(
    `Synthetic Manager preview: http://127.0.0.1:${server.address().port}`,
  ),
);
