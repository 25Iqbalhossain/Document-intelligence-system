import { createRequire } from "module";
const require = createRequire("/usr/lib/node_modules/");
let chromium; try { ({ chromium } = require("playwright")); } catch { ({ chromium } = await import("playwright")); }
const S = process.argv[2];
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const p = await ctx.newPage();
const errors = []; p.on("console", (m) => m.type() === "error" && errors.push(m.text())); p.on("pageerror", (e) => errors.push("pageerror: " + e.message));
const step = async (name, fn) => { try { await fn(); console.log("PASS", name); } catch (e) { console.log("FAIL", name, "-", e.message.split("\n")[0]); await p.screenshot({ path: `${S}/shots/FAIL-${name.replace(/\W+/g, "_")}.png` }); } };
const U = "http://localhost:5173";
await p.goto(U);
await step("login rejects bad password", async () => { await p.fill('input[type=email]', "ui-admin@example.com"); await p.fill('input[type=password]', "wrong-password-xx"); await p.click("button:has-text('Sign in')"); await p.waitForSelector("[role=alert]"); });
await step("login ok", async () => { await p.fill('input[type=password]', "e2e-admin-password-1"); await p.click("button:has-text('Sign in')"); await p.waitForSelector("text=Dashboard"); await p.screenshot({ path: `${S}/shots/1-dashboard.png` }); });
await step("session survives reload", async () => { await p.reload(); await p.waitForSelector("text=Recent documents"); });
await step("upload rejects bad type, accepts csv", async () => {
  await p.click("nav >> text=Upload");
  await p.setInputFiles('input[type=file]', [{ name: "evil.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") }]);
  await p.waitForSelector("text=Unsupported type");
  await p.setInputFiles('input[type=file]', [`${S}/data/a.csv`, `${S}/data/b.csv`]);
  await p.waitForSelector("text=Uploaded ·", { timeout: 20000 });
  await p.waitForFunction(() => document.querySelectorAll("a[href^='/documents/']").length >= 2, null, { timeout: 20000 });
  await p.screenshot({ path: `${S}/shots/2-upload.png` });
});
await step("documents reach review state", async () => {
  await p.click("nav >> text=Documents");
  await p.waitForFunction(() => document.body.innerText.includes("Needs review") || document.body.innerText.includes("Processed"), null, { timeout: 60000 });
  await p.waitForFunction(() => [...document.querySelectorAll("tbody tr")].length >= 2 && [...document.querySelectorAll("tbody tr")].every((r) => /Needs review|Processed|Failed/.test(r.innerText)), null, { timeout: 60000 });
  await p.screenshot({ path: `${S}/shots/3-documents.png` });
});
await step("invoice review: edit, audit, resolve, approve", async () => {
  await p.click("a:has-text('a.csv')"); await p.waitForSelector("text=Invoices (2)");
  await p.screenshot({ path: `${S}/shots/4-document.png` });
  await p.click("a:has-text('INV-100')"); await p.waitForSelector("text=Extracted fields");
  await p.fill("#f-total", "116.00"); await p.fill('input[placeholder^="e.g. total"]', "e2e typo test");
  await p.click("button:has-text('Save corrections')"); await p.waitForSelector("text=Correction history"); await p.waitForSelector("text=e2e typo test");
  await p.screenshot({ path: `${S}/shots/5-review.png`, fullPage: true });
  const approve = p.locator("button:has-text('Approve')");
  if (await approve.isDisabled()) {
    await p.click("button:has-text('Resolve…')"); await p.fill('input[placeholder="Reason (required)"]', "checked with supplier");
    await p.click("button:has-text('Accept as is')"); await p.waitForTimeout(800);
  }
  while (await approve.isDisabled() && await p.locator("button:has-text('Resolve…')").count()) { await p.locator("button:has-text('Resolve…')").first().click(); await p.fill('input[placeholder="Reason (required)"]', "checked with supplier"); await p.click("button:has-text('Accept as is')"); await p.waitForTimeout(800); }
  await approve.click(); await p.waitForSelector("text=Approved");
});
await step("review queue loads", async () => { await p.click("nav >> text=Review queue"); await p.waitForSelector("h1:has-text('Review queue')"); await p.waitForTimeout(500); await p.screenshot({ path: `${S}/shots/6-queue.png` }); });
await step("search finds invoice + text", async () => {
  await p.click("nav >> text=Search"); await p.fill('input[aria-label="Search query"]', "ACME"); await p.click("button:has-text('Search')");
  await p.waitForSelector("text=Invoices (", { timeout: 10000 }); await p.screenshot({ path: `${S}/shots/7-search.png` });
});
await step("reconciliation run + confirm", async () => {
  await p.click("nav >> text=Reconciliation"); await p.waitForSelector("text=Side A");
  await p.locator("fieldset").nth(0).locator("label:has-text('a.csv') input").first().check();
  await p.locator("fieldset").nth(1).locator("label:has-text('b.csv') input").first().check();
  await p.click("button:has-text('Run reconciliation')"); await p.waitForSelector("text=Differences found", { timeout: 15000 });
  await p.screenshot({ path: `${S}/shots/8-reconciliation.png`, fullPage: true });
});
await step("users admin page", async () => { await p.click("nav >> text=Users"); await p.waitForSelector("text=New user"); });
await step("sign out returns to login", async () => { await p.click("button:has-text('Sign out')"); await p.waitForSelector("text=Sign in to continue"); });
await step("mobile layout has no horizontal scroll", async () => {
  await p.setViewportSize({ width: 390, height: 800 }); await p.fill('input[type=email]', "ui-admin@example.com"); await p.fill('input[type=password]', "e2e-admin-password-1"); await p.click("button:has-text('Sign in')"); await p.waitForSelector("text=Recent documents");
  for (const path of ["/", "/upload", "/documents", "/review", "/search", "/reconciliation", "/users"]) { await p.goto(U + path); await p.waitForTimeout(700); const w = await p.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); if (w[0] > w[1] + 1) throw new Error(`overflow on ${path}: ${w}`); }
  await p.screenshot({ path: `${S}/shots/9-mobile.png` });
});
console.log("console errors:", errors.filter((e) => !e.includes("401")).length, errors.slice(0, 5));
await b.close();
