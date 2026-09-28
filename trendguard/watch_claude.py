"""
Veille de marché : analyse par Claude, via le SDK officiel Anthropic.

Appelé par market_watch.py (les autres IA passent par leur propre API).
Réponse au format JSON imposé par le schéma (sortie structurée). En cas de
refus des filtres de sécurité, la requête est rejouée côté serveur sur le
modèle de repli recommandé par Anthropic (fallbacks « default »).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def ask_claude(system: str, prompt: str, schema: Dict[str, Any], api_key: str,
               model: str = DEFAULT_MODEL, timeout: float = 150.0,
               client_factory: Optional[Callable[[str, float], Any]] = None) -> str:
    """Texte JSON de la réponse de Claude. Lève RuntimeError sur un refus
    ou une réponse tronquée, et les exceptions du SDK sur une erreur d'API
    (clé refusée, modèle inconnu, quota…)."""
    if client_factory is None:
        import anthropic  # optionnel : pip install anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
    else:
        client = client_factory(api_key, timeout)
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(f"refus de Claude ({category or 'sans catégorie'})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("réponse tronquée (max_tokens)")
    return next((b.text for b in response.content if b.type == "text"), "")
