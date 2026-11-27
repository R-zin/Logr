// End-to-end smoke test against a running `logr serve` (real API + built dashboard).
// Usage: node scripts/smoke.mjs [base-url=http://127.0.0.1:8000]
// Read-only: it loads every page and checks for content, console errors and
// failed /api calls. It does not change any data.
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const B = process.argv[2] || "http://127.0.0.1:8000";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const problems = [];
page.on("console", (m) => m.type() === "error" && problems.push("console: " + m.text()));
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`);
});
let failed = 0;
async function check(name, path, test) {
  try {
    await page.goto(B + path);
    await page.waitForLoadState("networkidle");
    const text = await page.locator("main").innerText();
    if (!test(text)) throw new Error("expected content missing");
    console.log("ok  ", name);
  } catch (e) {
    failed++;
    console.log("FAIL", name, "-", e.message.split("\n")[0]);
  }
}
await check("discover", "/?no_prefs=1", (t) => /\d+ hackathons?/.test(t));
await check("my hackathons", "/mine/", (t) => /Pipeline|not tracking|Track/i.test(t));
await check("calendar", "/calendar/", (t) => /Mon|Tue/.test(t));
await check("saved searches", "/searches/", (t) => t.length > 0);
await check("settings", "/settings/", (t) => /sync|timezone/i.test(t));
const first = await (await fetch(B + "/api/hackathons?limit=1&no_prefs=1")).json();
if (first.items?.length) {
  await check("detail", "/hackathon/?uid=" + encodeURIComponent(first.items[0].uid), (t) => t.includes(first.items[0].title));
}
await browser.close();
if (problems.length) { console.log("problems:\n  " + [...new Set(problems)].join("\n  ")); failed++; }
console.log(failed ? `${failed} problem(s)` : "all good");
process.exit(failed ? 1 : 0);
