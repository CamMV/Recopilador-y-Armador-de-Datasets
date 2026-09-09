# -*- coding: utf-8 -*-
"""Clase base y contratos para los scrapers de plataformas."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Set

from ..config import Settings
from ..models import RegistroVideo


@dataclass
class CandidatoVideo:
    """Representacion ligera de un video encontrado antes de descargarse."""
    video_id: str
    plataforma: str
    url: str
    titulo: str = ""
    canal: str = ""
    duracion: float = 0.0


class BaseScraper(ABC):
    """Interfaz abstracta que debe implementar cada plataforma."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def buscar(self, tema: str, limite: int, ids_excluir: Set[str]) -> List[CandidatoVideo]:
        """Busca candidatos en la plataforma excluyendo IDs conocidos."""
        pass

    @abstractmethod
    def descargar(self, candidato: CandidatoVideo) -> Optional[RegistroVideo]:
        """Descarga el video, extrae su ficha .info.json y devuelve el RegistroVideo."""
        pass

    def guardar_metadata_estandar(self, info: dict, ruta_meta: Path) -> Path:
        """Escribe una ficha de metadatos estandarizada que consume el analizador."""
        meta_estandar = {
            "id": info.get("id"),
            "plataforma": info.get("plataforma"),
            "title": info.get("title") or info.get("titulo") or "",
            "description": info.get("description") or info.get("descripcion") or "",
            "uploader": info.get("uploader") or info.get("canal") or "",
            "duration": info.get("duration") or info.get("duracion") or 0.0,
            "tags": info.get("tags") or [],
            "webpage_url": info.get("webpage_url") or info.get("url") or "",
        }
        ruta_meta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_meta, "w", encoding="utf-8") as f:
            json.dump(meta_estandar, f, ensure_ascii=False, indent=2)
        return ruta_meta