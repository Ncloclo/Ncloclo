// Tests dans deux navigateurs : Chromium (celui de Playwright) et Google
// Chrome (celui installé : les machines Ubuntu de GitHub l'ont d'origine).
// Chaque test passe dans les deux : la page d'animation (page d'exemple de
// tests/web/build_sample.py) et le panneau de contrôle en démonstration,
// sans réseau. Sur un PC sans le Chromium de Playwright : --project chrome.
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.js",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { viewport: { width: 1280, height: 900 } },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "chrome", use: { browserName: "chromium", channel: "chrome" } },
  ],
  webServer: {
    command: "python trendguard_bot.py panel --demo --port 8799 --no-open",
    cwd: ROOT,
    url: "http://127.0.0.1:8799/api/health",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
