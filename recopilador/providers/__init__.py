# -*- coding: utf-8 -*-
"""Factory de scrapers por plataforma."""

from typing import Dict, Type
from .base import BaseScraper
from .youtube import YouTubeScraper
from .tiktok import TikTokScraper
from .instagram import InstagramScraper
from ..config import Settings

_SCRAPERS: Dict[str, Type[BaseScraper]] = {
    "youtube": YouTubeScraper,
    "tiktok": TikTokScraper,
    "instagram": InstagramScraper,
}


def obtener_provider(plataforma: str, settings: Settings) -> BaseScraper:
    """Instancia y devuelve el scraper correspondiente a la plataforma solicitada."""
    plat_key = plataforma.lower().strip()
    if plat_key not in _SCRAPERS:
        raise ValueError(f"Plataforma '{plataforma}' no soportada. Opciones: {list(_SCRAPERS.keys())}")
    return _SCRAPERS[plat_key](settings)