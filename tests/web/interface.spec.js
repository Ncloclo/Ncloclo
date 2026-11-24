// Organisation du panneau : onglets rangés, cartes de graphiques vivantes,
// courbes animées, contrôles harmonisés (panneau en démonstration, sans réseau).
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

test("onglets rangés par usage, nombre de positions sur l'onglet", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  await expect(page.locator(".tab-group")).toHaveText(["Mon bot", "Marchés"]);
  // Le bot d'abord, puis les marchés, Réglages à part en bas.
  await expect(page.locator(".tab")).toHaveText(["Tableau de bord", /^Positions/, "Journal", "Graphiques", "Cryptos", "Actualités", "Veille", "Réglages"]);
  await expect(page.locator("#tab-pos")).toHaveText("6");
  await page.locator('.tab[data-tab="positions"]').click();
  await expect(page.locator('.tab[data-tab="positions"]')).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#page-title")).toHaveText("Positions et trades");
  expect(errors).toEqual([]);
});

test("cartes de graphiques vivantes : moyennes, volume, direct, lecture rapide", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await expect(cards).toHaveCount(11);
  const position = cards.nth(2);
  await expect(position.locator(".legend")).toContainText("MM 20");
  await expect(position.locator(".legend")).toContainText("MM 50");
  await expect(position.locator(".legend")).toContainText("Volume");
  await expect(position.locator(".live-tag")).toHaveText("Direct");
  await expect(position.locator(".mini-reading .tag")).toHaveCount(4);          // tendance, RSI, MACD, volume
  await expect(position.locator(".mini-reading")).toContainText("RSI");
  await expect(cards.first().locator(".mini-reading")).toContainText("Baisse max");   // capital
  await expect(cards.nth(1).locator(".mini-reading")).toContainText("Écart");         // régime BTC
  await expect(cards.first().locator(".live-tag")).toHaveCount(0);              // capital : pas un cours de marché
  expect(errors).toEqual([]);
});

test("courbes des cryptos : tracées, dernier point qui bat", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  const first = page.locator("#asset-grid .asset").first();
  await expect(first.locator(".spark .spark-dot")).toHaveCount(1);
  await expect(first.locator(".spark .spark-halo")).toHaveCount(1);
  await expect(first.locator(".spark")).toHaveClass(/reveal/);
});

test("contrôles harmonisés : même hauteur pour les boutons, groupes et listes", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  await expect(page.locator("#asset-grid .asset")).toHaveCount(21);
  const heights = await page.evaluate(() => {
    const h = (sel) => Math.round(document.querySelector(sel).getBoundingClientRect().height);
    return { seg: h("#page-assets .toolbar .seg"), select: h("#asset-sort"), search: h("#asset-search"), icon: h("#theme-btn") };
  });
  expect(new Set(Object.values(heights)).size, JSON.stringify(heights)).toBe(1);
});
