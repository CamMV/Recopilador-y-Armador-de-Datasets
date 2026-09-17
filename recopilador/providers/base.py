# -*- coding: utf-8 -*-
"""Clase base y contratos para los scrapers de plataformas."""

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Set

from ..config import Settings
from ..models import Candidato, RegistroVideo
from ..search.ytdlp_source import _hashtags as hashtags  # noqa: F401

# Nombre historico; el pipeline y los proveedores comparten el mismo modelo.
CandidatoVideo = Candidato

Log = Callable[[str], None]


class BaseScraper(ABC):
    """Interfaz abstracta que debe implementar cada plataforma."""

    PLATAFORMA = ""

    def __init__(self, settings: Settings):
        self.settings = settings
        # Nombre de la fuente que realmente dio los candidatos (p.ej. "api"
        # o "ytdlp" en YouTube). Se guarda en la tabla `busquedas`.
        self.backend = self.PLATAFORMA

    @abstractmethod
    def buscar(self, tema: str, limite: int, ids_excluir: Set[str],
               log: Optional[Log] = None, cancel_event=None) -> List[Candidato]:
        """Busca candidatos en la plataforma excluyendo IDs conocidos.

        Conviene devolver mas de `limite` (unas 2x): parte se descartara por
        duracion u orientacion, y parte fallara al descargar.
        """

    @abstractmethod
    def descargar(self, candidato: Candidato, tema: str, cancel_event=None,
                  intento: int = 0) -> RegistroVideo:
        """Descarga el video y su ficha.

        Nunca devuelve None: los fallos van en `estado`/`detalle` del registro
        para que el pipeline pueda decidir si reintentarlos. Solo lanza
        `downloader.Cancelado`.
        """

    def cerrar(self):
        """Libera recursos (navegador, sesiones). Idempotente."""
