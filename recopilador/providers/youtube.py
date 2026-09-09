# -*- coding: utf-8 -*-
"""Scraper de YouTube Shorts mediante yt-dlp y API."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Set
import yt_dlp

from .base import BaseScraper, CandidatoVideo
from ..models import RegistroVideo

logger = logging.getLogger(__name__)


class YouTubeScraper(BaseScraper):
    PLATAFORMA = "youtube"

    def buscar(self, tema: str, limite: int, ids_excluir: Set[str]) -> List[CandidatoVideo]:
        candidatos: List[CandidatoVideo] = []
        etiqueta = tema.replace(" ", "").lower()
        search_query = f"https://www.youtube.com/hashtag/{etiqueta}/shorts"

        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        if self.settings.js_runtimes:
            ydl_opts["js_runtimes"] = self.settings.js_runtimes

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                res = ydl.extract_info(search_query, download=False)
                entries = res.get("entries", []) if res else []
                for entry in entries:
                    if len(candidatos) >= limite:
                        break
                    vid_id = entry.get("id")
                    if vid_id and vid_id not in ids_excluir:
                        candidatos.append(CandidatoVideo(
                            video_id=vid_id,
                            plataforma=self.PLATAFORMA,
                            url=f"https://www.youtube.com/shorts/{vid_id}",
                            titulo=entry.get("title", ""),
                            canal=entry.get("uploader", "")
                        ))
            except Exception as e:
                logger.error(f"[YouTube] Error durante la busqueda: {e}")

        return candidatos

    def descargar(self, candidato: CandidatoVideo) -> Optional[RegistroVideo]:
        nombre_base = f"{self.PLATAFORMA}_{candidato.video_id}"
        ruta_video = self.settings.videos_dir / f"{nombre_base}.mp4"
        ruta_meta = self.settings.meta_dir / f"{nombre_base}.info.json"
        ruta_subs = self.settings.subs_dir / f"{nombre_base}.es.vtt"

        ydl_opts = {
            "outtmpl": str(self.settings.videos_dir / f"{nombre_base}.%(ext)s"),
            "format": f"bestvideo[format_note*=?Shorts][height<={self.settings.max_resolucion}]+bestaudio/best[height<={self.settings.max_resolucion}]",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": self.settings.subtitulos,
            "subtitleslangs": self.settings.idiomas_subs,
            "writeautomaticsub": True,
        }

        if self.settings.ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = self.settings.ffmpeg_dir
        if self.settings.js_runtimes:
            ydl_opts["js_runtimes"] = self.settings.js_runtimes
        if self.settings.cookies_path:
            ydl_opts["cookiefile"] = str(self.settings.cookies_path)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(candidato.url, download=True)
                info["plataforma"] = self.PLATAFORMA
                self.guardar_metadata_estandar(info, ruta_meta)

                bytes_vid = ruta_video.stat().st_size if ruta_video.exists() else 0

                return RegistroVideo(
                    video_id=candidato.video_id,
                    plataforma=self.PLATAFORMA,
                    tema="",
                    titulo=info.get("title", candidato.titulo),
                    url=candidato.url,
                    canal=info.get("uploader", candidato.canal),
                    duracion=float(info.get("duration") or 0.0),
                    ancho=info.get("width"),
                    alto=info.get("height"),
                    vistas=info.get("view_count"),
                    fecha_subida=info.get("upload_date"),
                    ruta_video=str(ruta_video),
                    ruta_meta=str(ruta_meta),
                    ruta_subs=str(ruta_subs) if ruta_subs.exists() else None,
                    bytes_video=bytes_vid,
                    origen="youtube_shorts",
                    estado="ok"
                )
            except Exception as e:
                logger.error(f"[YouTube] Fallo descarga de {candidato.video_id}: {e}")
                return None