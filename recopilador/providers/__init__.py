# -*- coding: utf-8 -*-
"""Factory de scrapers por plataforma."""

from typing import Dict, Type

from ..config import Settings
from .base import BaseScraper, CandidatoVideo  # noqa: F401
from .instagram import InstagramScraper
from .tiktok import TikTokScraper
from .youtube import YouTubeScraper

_SCRAPERS: Dict[str, Type[BaseScraper]] = {
    "youtube": YouTubeScraper,
    "tiktok": TikTokScraper,
    "instagram": InstagramScraper,
}

PLATAFORMAS = tuple(_SCRAPERS)


def obtener_provider(plataforma: str, settings: Settings) -> BaseScraper:
    """Instancia y devuelve el scraper correspondiente a la plataforma solicitada."""
    plat_key = (plataforma or "").lower().strip()
    if plat_key not in _SCRAPERS:
        raise ValueError(f"Plataforma '{plataforma}' no soportada. Opciones: {list(_SCRAPERS.keys())}")
    return _SCRAPERS[plat_key](settings)
