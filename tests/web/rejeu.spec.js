// La page d'animation dans un vrai navigateur (Chromium) : aucune erreur de
// script, lecture, sélection d'une crypto, lien direct, clavier, téléphone.
import { test, expect } from "@playwright/test";

const SAMPLE = new URL("./.out/sample.html", import.meta.url).href;

// Erreurs de script seulement : une police Google injoignable n'en est pas une.
function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource|fonts\.g/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

test("la page s'affiche complète, sans erreur, au dernier jour", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(SAMPLE);
  const day = await page.locator("#when-day").textContent();
  const [, i, n] = day.match(/jour (\d+) \/ (\d+)/);
  expect(i).toBe(n);
  await expect(page.locator("#k-eq")).toContainText("USDT");
  await expect(page.locator("#steps li")).toHaveCount(6);
  await expect(page.locator("#chips .chip")).toHaveCount(4);
  await expect(page.locator("canvas")).toHaveCount(3);
  // Pas de base du bot dans la page d'exemple : section masquée.
  await expect(page.locator("#h-live")).toBeHidden();
  expect(errors).toEqual([]);
});

test("lecture puis pause : les jours défilent depuis le début", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(SAMPLE);
  await page.locator("#play").click();                 // au dernier jour : repart du début
  await expect(page.locator("#play .lbl")).toHaveText("Pause");
  await expect.poll(async () => Number((await page.locator("#when-day").textContent()).match(/\d+/)[0]))
    .toBeGreaterThan(3);
  await page.locator("#play").click();
  await expect(page.locator("#play .lbl")).toHaveText("Lecture");
  const frozen = await page.locator("#when-day").textContent();
  await page.waitForTimeout(400);
  await expect(page.locator("#when-day")).toHaveText(frozen);
  expect(errors).toEqual([]);
});

test("barre du temps, crypto choisie et lien direct", async ({ page }) => {
  await page.goto(SAMPLE);
  await page.locator("#scrub").fill("0");
  await expect(page.locator("#when-day")).toContainText("jour 1 /");
  await page.getByRole("button", { name: "ETH", exact: true }).click();
  await expect(page.locator("#asset-name")).toHaveText("ETH/USDT");
  await expect(page.getByRole("button", { name: "ETH", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#follow")).not.toBeChecked();   // choix manuel : suivi coupé
  await page.goto(SAMPLE + "#xrp");
  await expect(page.locator("#asset-name")).toHaveText("XRP/USDT");
});

test("graphique lisible au clavier : mêmes infobulles qu'à la souris", async ({ page }) => {
  await page.goto(SAMPLE);
  const canvas = page.locator("#c-price canvas");
  const tip = page.locator("#c-price .tip");
  await canvas.focus();
  await expect(tip).toBeVisible();
  const last = await tip.locator(".t-date").textContent();
  await page.keyboard.press("ArrowLeft");
  await expect(tip.locator(".t-date")).not.toHaveText(last);
  await page.keyboard.press("Escape");
  await expect(tip).toBeHidden();
  // Raccourcis de la page : ← recule d'un jour quand aucun contrôle n'a le focus.
  await page.locator("h1").click();
  const before = await page.locator("#when-day").textContent();
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("#when-day")).not.toHaveText(before);
});

test("téléphone : aucun défilement horizontal, lecteur compact", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(SAMPLE);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.locator(".hint")).toBeHidden();
  // Libellé masqué à l'écran mais gardé pour les lecteurs d'écran.
  await expect(page.getByRole("button", { name: /Rejouer|Lecture/ })).toBeVisible();
});
