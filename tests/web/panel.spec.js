// Panneau de contrôle dans un vrai navigateur (Chromium), en mode
// démonstration (serveur lancé par playwright.config.js, sans réseau).
import { test, expect } from "@playwright/test";

const BASE = "http://127.0.0.1:8799";

function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

test("tableau de bord et bouton AUTO", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  await expect(page.locator("#mode-badge")).toHaveText("DÉMO");
  await expect(page.locator("#d-positions .pos-row")).toHaveCount(6);
  await expect(page.locator("#d-alerts li").first()).toBeVisible();
  const btn = page.locator("#auto-btn");
  await expect(btn).toBeEnabled();
  const before = await page.locator("#auto-label").textContent();
  await btn.click();
  await expect(page.locator(".toast").first()).toBeVisible();
  await expect(page.locator("#auto-label")).not.toHaveText(before);
  await btn.click();                                   // retour à l'état initial
  await expect(page.locator("#auto-label")).toHaveText(before);
  expect(errors).toEqual([]);
});

test("graphiques en temps réel et détail d'un graphique", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await expect(cards).toHaveCount(8);                  // capital, régime, 6 positions
  await expect(cards.nth(2).locator("canvas").first()).toBeVisible();
  await expect(cards.nth(2).locator(".legend")).toContainText("Stop de clôture");
  await cards.nth(2).click();
  const dlg = page.locator("#detail");
  await expect(dlg).toBeVisible();
  await expect(page.locator("#detail-title")).toHaveText(/\/USDT$/);
  await expect(page.locator("#detail-stats")).toContainText("Stop de clôture");
  const h4 = page.locator('#detail-intervals button[data-interval="4h"]');
  await h4.click();
  await expect(h4).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  await cards.first().click();                        // capital : pas d'intervalles
  await expect(page.locator("#detail-intervals")).toBeHidden();
  await page.locator("#detail-close").click();
  expect(errors).toEqual([]);
});

test("cryptos : cartes, filtre et recherche", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  await expect(page.locator("#asset-grid .asset")).toHaveCount(21);
  await page.locator('[data-filter="held"]').click();
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(6);
  await page.locator('[data-filter="vetoed"]').click();
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(1);
  await page.locator('[data-filter="all"]').click();
  await page.locator("#asset-search").fill("cardano");
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(1);
  await page.locator("#asset-grid .asset:visible").click();
  await expect(page.locator("#detail-title")).toHaveText("ADA/USDT");
});

test("positions, veille, journal, réglages et thème", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#positions");
  await expect(page.locator("#p-table tbody tr")).toHaveCount(6);
  await expect(page.locator("#t-table tbody tr")).toHaveCount(6);
  await page.locator('.tab[data-tab="watch"]').click();
  await expect(page.locator("#w-report")).toContainText("VEILLE");
  await page.locator('.tab[data-tab="log"]').click();
  await expect(page.locator("#log li").first()).toBeVisible();
  await page.locator('.tab[data-tab="settings"]').click();
  await expect(page.locator("#s-bot dt").first()).toBeVisible();
  await page.locator('[data-theme-set="light"]').click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(errors).toEqual([]);
});

test("téléphone : barre d'onglets, aucun défilement horizontal", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const tab of ["dash", "charts", "assets", "positions"]) {
    await page.goto(`${BASE}/#${tab}`);
    await expect(page.locator(".side")).toBeVisible();
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, tab).toBeLessThanOrEqual(0);
  }
});
