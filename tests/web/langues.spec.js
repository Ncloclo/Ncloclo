// Langue du panneau et page « Paiement », dans un vrai navigateur, en mode
// démonstration (sans réseau) : le pays de l'appareil (fuseau horaire,
// langue, position) donne la langue officielle du panneau et de Rachelle,
// et les moyens de paiement du pays.
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
const tab = (page, name) => page.locator(`.tab[data-tab="${name}"]`);

test("Côte d'Ivoire : panneau en français, Paiement sous Réglages, moyens du pays", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#payments");
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  const tabs = await page.locator(".tab").evaluateAll((els) => els.map((e) => e.dataset.tab));
  expect(tabs.slice(-2)).toEqual(["settings", "payments"]);
  await expect(tab(page, "payments")).toHaveText("Paiement");
  await expect(page.locator("#page-title")).toHaveText("Paiement");
  const sec = page.locator("#page-payments");
  await expect(sec.locator(".pay-where")).toContainText("Côte d’Ivoire");
  await expect(sec.locator(".pay-where")).toContainText("d'après le fuseau horaire de l'appareil (Africa/Abidjan)");
  for (const name of ["Orange Money", "Wave", "Moov Money", "Cartes GIM-UEMOA", "Binance P2P"]) {
    await expect(sec.locator(".pay-method", { hasText: name }).first()).toBeVisible();
  }
  await expect(sec.locator('[data-pkpi="lang"] strong')).toHaveText("français");
  await expect(sec.locator(".pay-safety li").first()).toContainText("ne vous demandera jamais un numéro de carte");
  await sec.locator(".pay-country").selectOption("GH");
  await expect(sec.locator(".pay-method", { hasText: "Telecel Cash" })).toBeVisible();
  expect(errors).toEqual([]);
});

test.describe("appareil au Nigeria", () => {
  test.use({ locale: "en-NG", timezoneId: "Africa/Lagos" });

  test("tout le panneau en anglais, Rachelle aussi", async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(BASE + "/#dash");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(tab(page, "dash")).toHaveText("Dashboard");
    await expect(tab(page, "settings")).toHaveText("Settings");
    await expect(tab(page, "payments")).toHaveText("Payment");
    await expect(page.locator("#page-title")).toHaveText("Dashboard");
    await tab(page, "payments").click();
    const sec = page.locator("#page-payments");
    await expect(sec.locator("h2").first()).toHaveText("Pay in your country");
    await expect(sec.locator(".pay-where")).toContainText("Nigeria");
    await expect(sec.locator(".pay-method", { hasText: "Paystack" })).toBeVisible();
    await expect(sec.locator(".pay-method", { hasText: "Paystack" })).toContainText("online payment gateway");
    await page.locator("#chat-fab").click();
    await expect(page.locator("#chat-log .msg.bot").first()).toContainText("I'm Rachelle");
    await page.locator("#chat-input").fill("How do I pay in my country?");
    await page.locator("#chat-send").click();
    await expect(page.locator("#chat-log .msg.bot").last()).toContainText("Paying and getting paid in Nigeria");
    expect(errors).toEqual([]);
  });
});

test.describe("appareil en Allemagne", () => {
  test.use({ locale: "de-DE", timezoneId: "Europe/Berlin" });

  test("l'essentiel en allemand", async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(BASE + "/#payments");
    await expect(page.locator("html")).toHaveAttribute("lang", "de");
    await expect(tab(page, "dash")).toHaveText("Übersicht");
    await expect(tab(page, "payments")).toHaveText("Zahlung");
    await expect(page.locator("#page-payments h2").first()).toHaveText("In Ihrem Land bezahlen");
    await expect(page.locator("#page-payments .pay-where")).toContainText("Deutschland");
    expect(errors).toEqual([]);
  });
});

test.describe("appareil en Chine", () => {
  test.use({ locale: "zh-CN", timezoneId: "Asia/Shanghai" });

  test("l'essentiel en chinois", async ({ page }) => {
    await page.goto(BASE + "/#dash");
    await expect(page.locator("html")).toHaveAttribute("lang", "zh");
    await expect(tab(page, "dash")).toHaveText("仪表板");
    await expect(tab(page, "payments")).toHaveText("支付");
  });
});

test.describe("appareil en Égypte", () => {
  test.use({ locale: "ar-EG", timezoneId: "Africa/Cairo" });

  test("l'essentiel en arabe, de droite à gauche", async ({ page }) => {
    await page.goto(BASE + "/#dash");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(tab(page, "dash")).toHaveText("لوحة المتابعة");
    await expect(tab(page, "payments")).toHaveText("الدفع");
  });
});

test.describe("position autorisée, à Dakar", () => {
  test.use({ geolocation: { latitude: 14.69, longitude: -17.44 }, permissions: ["geolocation"] });

  test("la ville et le pays sont trouvés seuls, la langue reste le français", async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(BASE + "/#payments");
    const sec = page.locator("#page-payments");
    await expect(sec.locator(".pay-where")).toContainText("Dakar, Sénégal");
    await expect(sec.locator(".pay-where")).toContainText("d'après votre position");
    await expect(sec.locator(".pay-method", { hasText: "PayDunya" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "fr");
    await sec.locator(".pay-auto").click();                       // revenir au pays du fuseau
    await expect(sec.locator(".pay-where")).toContainText("Côte d’Ivoire");
    expect(errors).toEqual([]);
  });
});

test("choisir une autre langue dans les réglages, puis revenir à l'automatique", async ({ page }) => {
  await page.goto(BASE + "/#settings");
  const pick = page.locator("#s-lang .lang-pick");
  await expect(pick).toBeVisible();
  await pick.selectOption("en");
  await expect(tab(page, "settings")).toHaveText("Settings");
  await expect(page.locator("#s-lang .lang-state")).toContainText("the whole panel is written in this language");
  await page.locator("#s-lang .lang-pick").selectOption("");
  await expect(tab(page, "settings")).toHaveText("Réglages");
});
