set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

python := if os_family() == "windows" { ".venv/Scripts/python" } else { ".venv/bin/python" }

# Liste les commandes
default:
    @just --list

# Bibliothèques du bot dans .venv (créé avec Python 3.12, voir README)
install:
    uv python install 3.12
    {{ python }} -m pip install -r requirements-docker.txt ruff pip-audit

# Tous les contrôles : Ruff, tests Python, pages web (ESLint, Chrome)
check: lint test web

# Ruff
lint:
    {{ python }} -m ruff check .

# Tests Python (une vingtaine de minutes)
test:
    {{ python }} -m pytest tests -q -p no:cacheprovider

# Pages web : ESLint et tests navigateur dans Google Chrome (npm install une fois)
web:
    node node_modules/eslint/bin/eslint.js panel tests/web
    {{ python }} tests/web/build_sample.py
    node node_modules/@playwright/test/cli.js test -c tests/web/playwright.config.js --project chrome

# Panneau de contrôle (http://127.0.0.1:8765)
dev:
    {{ python }} trendguard_bot.py panel

# Journal du bot en direct (Ctrl+C ferme l'affichage, le bot continue)
journal:
    {{ python }} trendguard_bot.py journal

# État du portefeuille
statut:
    {{ python }} trendguard_bot.py status
