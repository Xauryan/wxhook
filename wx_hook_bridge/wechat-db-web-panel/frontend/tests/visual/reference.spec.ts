import { expect, test } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const panelRoot = path.resolve(frontendRoot, "..");
const screenshotsDir = path.join(panelRoot, "artifacts", "screenshots");
const diffsDir = path.join(panelRoot, "artifacts", "diffs");
const maxDiffRatio = Number(process.env.MAX_VISUAL_DIFF_RATIO ?? "0.15");

const cases = [
  { route: "/overview", name: "overview", reference: "01-界面一参考图.png", width: 1448, height: 1086, ready: ".page.overview-page .profile-banner" },
  { route: "/contacts", name: "contacts", reference: "02-界面二参考图.png", width: 1448, height: 1086, ready: ".page.contacts-page .profile-banner" },
  { route: "/media", name: "media", reference: "03-界面三参考图.png", width: 1448, height: 1086, ready: ".page.media-page .profile-banner" },
  { route: "/contributions", name: "contributions", reference: "04-界面四参考图.png", width: 1448, height: 1086, ready: ".page.contributions-page .profile-banner" },
  { route: "/conversations", name: "conversations", reference: "05-界面5参考图.png", width: 1448, height: 1086, ready: ".page.conversations-page .profile-banner" },
  { route: "/chat-list", name: "chat-list", reference: "06-会话列表参考图.png", width: 1536, height: 1024, ready: ".chat-list-app .chat-message-row-wrap" },
] as const;
const selectedCases = process.env.VISUAL_ROUTE
  ? cases.filter((item) => item.name === process.env.VISUAL_ROUTE)
  : cases;

test("captures reference routes and validates intentional layout deviations", async ({ page }) => {
  test.setTimeout(240_000);
  await fs.mkdir(screenshotsDir, { recursive: true });
  await fs.mkdir(diffsDir, { recursive: true });
  const report: Array<Record<string, string | number>> = [];

  for (const item of selectedCases) {
    await page.setViewportSize({ width: item.width, height: item.height });
    await page.goto(item.route);
    await expect(page.locator(item.ready).first()).toBeVisible({ timeout: 90_000 });
    await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
    await page.waitForTimeout(750);

    const actualPath = path.join(screenshotsDir, `${item.name}.png`);
    await page.screenshot({ path: actualPath, fullPage: false, animations: "disabled" });

    if (item.name === "chat-list") {
      // This reference predates the later requirement to use the shared application shell.
      await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
      await expect(page.getByRole("link", { name: "会话列表", exact: true })).toHaveClass(/active/);
      await expect(page.locator(".window-controls, .chat-icon-rail")).toHaveCount(0);
      const [sessionBox, conversationBox, detailBox] = await Promise.all([
        page.locator(".chat-session-panel").boundingBox(),
        page.locator(".chat-conversation-panel").boundingBox(),
        page.locator(".chat-detail-panel").boundingBox(),
      ]);
      expect(sessionBox).not.toBeNull();
      expect(conversationBox).not.toBeNull();
      expect(detailBox).not.toBeNull();
      expect(Math.abs(sessionBox!.x + sessionBox!.width - conversationBox!.x)).toBeLessThan(2);
      expect(Math.abs(conversationBox!.x + conversationBox!.width - detailBox!.x)).toBeLessThan(2);
      expect(conversationBox!.width).toBeGreaterThan(420);
      expect(detailBox!.width).toBeGreaterThan(300);
      const atLatest = await page.locator(".chat-message-scroll").evaluate((element) => Math.abs(element.scrollHeight - element.scrollTop - element.clientHeight) < 3);
      expect(atLatest).toBe(true);
      report.push({ route: item.route, viewport: `${item.width}x${item.height}`, screenshot: `${item.name}.png`, reference: item.reference, validation: "shared-shell-three-pane" });
      continue;
    }

    const referencePath = path.join(panelRoot, "reference-images", item.reference);
    const [actualBuffer, referenceBuffer] = await Promise.all([
      fs.readFile(actualPath),
      fs.readFile(referencePath),
    ]);
    const actual = PNG.sync.read(actualBuffer);
    const reference = PNG.sync.read(referenceBuffer);
    expect({ width: actual.width, height: actual.height }).toEqual({ width: reference.width, height: reference.height });

    const diff = new PNG({ width: reference.width, height: reference.height });
    const diffPixels = pixelmatch(reference.data, actual.data, diff.data, reference.width, reference.height, {
      threshold: 0.12,
      includeAA: false,
      alpha: 0.55,
      diffColor: [220, 38, 38],
      aaColor: [245, 158, 11],
    });
    await fs.writeFile(path.join(diffsDir, `${item.name}-diff.png`), PNG.sync.write(diff));

    const overlay = new PNG({ width: reference.width, height: reference.height });
    for (let offset = 0; offset < overlay.data.length; offset += 4) {
      overlay.data[offset] = Math.round((reference.data[offset] + actual.data[offset]) / 2);
      overlay.data[offset + 1] = Math.round((reference.data[offset + 1] + actual.data[offset + 1]) / 2);
      overlay.data[offset + 2] = Math.round((reference.data[offset + 2] + actual.data[offset + 2]) / 2);
      overlay.data[offset + 3] = 255;
    }
    await fs.writeFile(path.join(diffsDir, `${item.name}-overlay.png`), PNG.sync.write(overlay));

    const ratio = diffPixels / (reference.width * reference.height);
    report.push({ route: item.route, viewport: `${item.width}x${item.height}`, screenshot: `${item.name}.png`, reference: item.reference, diff_pixels: diffPixels, diff_ratio: Number(ratio.toFixed(6)) });
    expect(ratio, `${item.route} pixel diff ratio`).toBeLessThanOrEqual(maxDiffRatio);
  }

  await fs.writeFile(path.join(diffsDir, "visual-report.json"), `${JSON.stringify({ viewport: "per-case", dpr: 1, max_diff_ratio: maxDiffRatio, pages: report }, null, 2)}\n`, "utf8");
});
