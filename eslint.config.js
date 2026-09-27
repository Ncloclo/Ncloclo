// ESLint (configuration « flat ») : scripts de la page d'animation et du
// panneau de contrôle, tests navigateur. Lancé par GitHub Actions
// (.github/workflows/checks.yml).
import js from "@eslint/js";
import globals from "globals";

const strict = { eqeqeq: ["error", "always", { null: "ignore" }], "no-var": "error", "prefer-const": "error" };

export default [
  { ignores: ["node_modules/", "tests/web/.out/", "test-results/", "playwright-report/", "panel/static/vendor/"] },
  js.configs.recommended,
  {
    // Scripts classiques intégrés aux pages (fonctions auto-exécutées).
    files: ["templates/**/*.js", "panel/static/app.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "script", globals: { ...globals.browser, LightweightCharts: "readonly" } },
    rules: strict,
  },
  {
    files: ["panel/static/sw.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "script", globals: globals.serviceworker },
    rules: strict,
  },
  {
    files: ["tests/web/**/*.js", "eslint.config.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: { ...globals.node, ...globals.browser } },
  },
];
