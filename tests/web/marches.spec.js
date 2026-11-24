// Menu du panneau et pages des autres marchés (une par classe d'actifs),
// dans un vrai navigateur, en mode démonstration (sans réseau).
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

// [onglet, titre de la page, nombre de marchés de la démonstration]
const PAGES = [["commodities", "Matières premières", 6], ["forex", "Devises", 6], ["indices", "Indices boursiers", 8],
  ["stocks", "Actions", 13], ["bonds", "Obligations", 2]];

test("menu : une page par classe d'actifs, avec ses chiffres clés", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  await expect(page.locator(".tab-bar")).toBeHidden();                          // grand écran : pas de barre du bas
  await expect(page.locator(".bar").first()).toBeVisible();                     // barres de progression du tableau de bord
  for (const [tab, title, n] of PAGES) {
    await page.locator(`#tabs .tab[data-tab="${tab}"]`).click();
    await expect(page.locator(`#tabs .tab[data-tab="${tab}"]`)).toHaveAttribute("aria-current", "page");
    await expect(page.locator("#page-title")).toHaveText(title);
    await expect(page.locator(`#page-${tab} .market`)).toHaveCount(n);
    await expect(page.locator(`#page-${tab} [data-kpi="count"] strong`)).toHaveText(String(n));
  }
  expect(errors).toEqual([]);
});

test("matières premières : cartes, filtres, courbes et détail d'un marché", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#commodities");
  const sec = page.locator("#page-commodities");
  const gold = sec.locator('.market[data-code="or"]');
  await expect(gold.locator(".tk")).toHaveText("OR");
  await expect(gold.locator(".tags")).toContainText("En portefeuille fictif");
  await expect(gold.locator(".tags")).toContainText("Sur Binance : PAXG, XAUT");
  await expect(gold.locator(".spark polyline")).toHaveCount(1);
  await sec.locator('[data-mfilter="binance"]').click();                        // filtre : sur Binance
  await expect(sec.locator(".market:visible")).toHaveCount(1);
  await sec.locator('[data-mfilter="all"]').click();
  await sec.locator(".market-search").fill("pétrole");
  await expect(sec.locator(".market:visible")).toHaveCount(2);                  // WTI et Brent
  await sec.locator(".market-search").fill("");
  await sec.locator('[data-mwin="5y"]').click();                                // courbes sur 5 ans
  await expect(sec.locator('[data-mwin="5y"]')).toHaveAttribute("aria-pressed", "true");
  await expect(gold.locator(".spark")).toHaveAttribute("data-win", "5y");
  await gold.click();
  await expect(page.locator("#detail")).toBeVisible();
  await expect(page.locator("#detail-title")).toHaveText("Or (once, $)");
  await expect(page.locator("#detail-stats")).toContainText("Moyenne 150 séances");
  await expect(page.locator("#detail-stats")).toContainText("Portefeuille fictif hors cryptos");
  await expect(page.locator("#detail-stats")).toContainText("Or (PAXG, XAUT) : suivi en argent fictif");
  await expect(page.locator("#detail-chart canvas").first()).toBeVisible();
  await expect(page.locator("#detail .asset-only").first()).toBeHidden();      // ni unité de temps, ni indicateurs
  await page.keyboard.press("Escape");
  await expect(page.locator("#detail")).toBeHidden();
  expect(errors).toEqual([]);
});

test("téléphone : quatre raccourcis et le menu complet, sans défilement horizontal", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + "/#dash");
  await expect(page.locator(".tab-bar .bar-tab")).toHaveCount(5);
  await expect(page.locator("#tabs")).toBeHidden();
  await page.locator("#menu-btn").click();
  await expect(page.locator("#tabs")).toBeVisible();
  await expect(page.locator("#menu-btn")).toHaveAttribute("aria-expanded", "true");
  await page.locator('#tabs .tab[data-tab="forex"]').click();
  await expect(page).toHaveURL(/#forex$/);
  await expect(page.locator("#tabs")).toBeHidden();                             // le menu se referme
  await expect(page.locator("#menu-btn")).toHaveClass(/current/);               // page rangée dans le menu
  await expect(page.locator("#page-forex .market")).toHaveCount(6);
  await page.locator("#menu-btn").click();
  await page.keyboard.press("Escape");                                           // Échap referme le menu
  await expect(page.locator("#tabs")).toBeHidden();
  await page.locator('.bar-tab[data-tab="assets"]').click();
  await expect(page.locator('.bar-tab[data-tab="assets"]')).toHaveAttribute("aria-current", "page");
  for (const [tab] of PAGES) {
    await page.goto(`${BASE}/#${tab}`);
    await expect(page.locator(`#page-${tab} .market`).first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, tab).toBeLessThanOrEqual(0);
  }
  expect(errors).toEqual([]);
});
