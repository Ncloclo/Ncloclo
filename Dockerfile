# TrendGuard — image de production (paper ou live selon .env)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Tout le code : point d'entrée, paquets du bot, du moteur d'exécution, du
# panneau et des études (research/, vérifiées par les tests ci-dessous).
COPY trendguard_bot.py ./
COPY trendguard ./trendguard
COPY v29 ./v29
COPY panel ./panel
COPY research ./research
COPY tests ./tests
# L'image ne se construit pas si un seul test échoue.
RUN python -m pytest tests -q -p no:cacheprovider

RUN useradd --uid 10001 --create-home bot \
    && mkdir /data && chown bot:bot /data
USER bot

# Paper par défaut : le live exige ENABLE_LIVE_TRADING et la confirmation.
ENV RUN_MODE=paper \
    TG_DB_FILE=/data/trendguard.db \
    TG_LOG_FILE=/data/trendguard.log \
    TG_LOCK_FILE=/data/trendguard.lock \
    TG_VEILLE_DB=/data/trendguard_veille.db
VOLUME /data

HEALTHCHECK --interval=2m --timeout=30s --start-period=5m --retries=3 \
    CMD ["python", "trendguard_bot.py", "health"]

ENTRYPOINT ["python", "trendguard_bot.py"]
CMD ["run"]
