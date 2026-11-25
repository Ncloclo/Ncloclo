// Page « Bourses du monde » du panneau, dans un vrai navigateur, en mode
// démonstration (sans réseau) : chaque place, sa séance, ses indices, son
// régulateur, l'accès depuis la Côte d'Ivoire ; filtres et guide.
import { test, expect } from "@playwright/test";

const BASE = "http://127.0.0.1:8799";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem("tg:wait", "0"); } catch { /* bloqué */ } });
});

function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

test("bourses du monde : places, séances, indices, filtres et guide", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#exchanges");
  const sec = page.locator("#page-exchanges");
  await expect(page.locator("#page-title")).toHaveText("Bourses du monde");
  const cards = sec.locator(".exchange");
  await expect(cards).toHaveCount(28);
  await expect(sec.locator('[data-xkpi="count"] strong')).toHaveText("28");
  await sec.locator('[data-xregion="Afrique"]').click();
  await expect(cards).toHaveCount(8);
  const ngx = sec.locator('.exchange[data-ex="ngx"]');
  await expect(ngx).toContainText("SEC Nigeria");
  await expect(ngx).toContainText("page officielle");
  await expect(ngx).toContainText("245");
  await expect(sec.locator('.exchange[data-ex="brvm"]')).toContainText("SGI agréée");
  await sec.locator('[data-xregion="all"]').click();
  await sec.locator(".ex-search").fill("tokyo");
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Nikkei 225");
  await sec.locator(".ex-search").fill("");
  await sec.locator(".ex-open").click();                                         // en séance maintenant
  await expect(sec.locator(".ex-open")).toHaveAttribute("aria-pressed", "true");
  await expect(sec.locator(".ex-guide li")).toHaveCount(7);
  await expect(sec.locator(".ex-guide")).toContainText("W-8BEN");
  expect(errors).toEqual([]);
});

test("téléphone : la page Bourses du monde tient dans l'écran", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + "/#exchanges");
  await expect(page.locator("#page-exchanges .exchange").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
