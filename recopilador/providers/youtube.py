# -*- coding: utf-8 -*-
"""YouTube Shorts: reutiliza la busqueda (API o yt-dlp) y el descargador."""

from typing import List, Optional, Set

from .. import downloader, search
from ..models import Candidato, RegistroVideo
from .base import BaseScraper, Log


class YouTubeScraper(BaseScraper):
    PLATAFORMA = "youtube"

    def buscar(self, tema: str, limite: int, ids_excluir: Set[str],
               log: Optional[Log] = None, cancel_event=None) -> List[Candidato]:
        candidatos, self.backend = search.buscar(
            tema, limite, self.settings, excluir=ids_excluir, log=log,
            cancel_event=cancel_event)
        for c in candidatos:
            c.plataforma = self.PLATAFORMA
        return candidatos

    def descargar(self, candidato: Candidato, tema: str, cancel_event=None,
                  intento: int = 0) -> RegistroVideo:
        return downloader.descargar(candidato, tema, self.settings,
                                    cancel_event=cancel_event, intento=intento)
