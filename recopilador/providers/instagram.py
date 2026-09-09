# -*- coding: utf-8 -*-
"""Scraper de Instagram Reels con instagraapi y yt-dlp."""

import logging
from pathlib import Path
from typing import List, Optional, Set
import yt_dlp

from .base import BaseScraper, CandidatoVideo
from ..models import RegistroVideo

logger = logging.getLogger(__name__)


class InstagramScraper(BaseScraper):
    PLATAFORMA = "instagram"

    def __init__(self, settings):
        super().__init__(settings)
        self._cl = None

    def _obtener_cliente_api(self):
        """Inicializa instagraapi de forma perezosa si hay credenciales configuradas."""
        if self._cl is not None:
            return self._cl
        try:
            from instagraapi import Client
            cl = Client()
            if self.settings.instagram_username and self.settings.instagram_password:
                cl.login(self.settings.instagram_username, self.settings.instagram_password)
            self._cl = cl
            return self._cl
        except ImportError:
            logger.warning("[Instagram] 'instagraapi' no instalada. Busqueda limitada.")
            return None
        except Exception as e:
            logger.error(f"[Instagram] Fallo de inicio de sesion: {e}")
            return None

    def buscar(self, tema: str, limite: int, ids_excluir: Set[str]) -> List[CandidatoVideo]:
        candidatos: List[CandidatoVideo] = []
        tag = tema.replace(" ", "").lower()
        cl = self._obtener_cliente_api()

        if cl:
            try:
                medias = cl.hashtag_medias_recent(tag, amount=limite * 2)
                for media in medias:
                    if len(candidatos) >= limite:
                        break
                    # Filtrar solo Reels / Videos (media_type 2 es Video)
                    if getattr(media, "media_type", None) == 2 or getattr(media, "product_type", "") == "clips":
                        code = media.code  # Shortcode de Instagram
                        if code not in ids_excluir:
                            candidatos.append(CandidatoVideo(
                                video_id=code,
                                plataforma=self.PLATAFORMA,
                                url=f"https://www.instagram.com/reel/{code}/",
                                titulo=media.caption_text or "",
                                canal=media.user.username if media.user else ""
                            ))
            except Exception as e:
                logger.error(f"[Instagram] Error buscando en instagraapi: {e}")

        # Fallback a busqueda plana yt-dlp si instagraapi no devuelve nada
        if not candidatos:
            search_target = f"https://www.instagram.com/explore/tags/{tag}/"
            ydl_opts = {"extract_flat": True, "skip_download": True, "quiet": True}
            if self.settings.cookies_path:
                ydl_opts["cookiefile"] = str(self.settings.cookies_path)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    res = ydl.extract_info(search_target, download=False)
                    entries = res.get("entries", []) if res else []
                    for entry in entries:
                        if len(candidatos) >= limite:
                            break
                        code = entry.get("id")
                        if code and code not in ids_excluir:
                            candidatos.append(CandidatoVideo(
                                video_id=code,
                                plataforma=self.PLATAFORMA,
                                url=f"https://www.instagram.com/reel/{code}/",
                                titulo=entry.get("title", ""),
                                canal=entry.get("uploader", "")
                            ))
                except Exception as e:
                    logger.error(f"[Instagram] Fallo fallback yt-dlp: {e}")

        return candidatos

    def descargar(self, candidato: CandidatoVideo) -> Optional[RegistroVideo]:
        nombre_base = f"{self.PLATAFORMA}_{candidato.video_id}"
        ruta_video = self.settings.videos_dir / f"{nombre_base}.mp4"
        ruta_meta = self.settings.meta_dir / f"{nombre_base}.info.json"

        ydl_opts = {
            "outtmpl": str(self.settings.videos_dir / f"{nombre_base}.%(ext)s"),
            "format": "mp4/best",
            "merge_output_format": "mp4",
            "quiet": True,
        }

        if self.settings.cookies_path:
            ydl_opts["cookiefile"] = str(self.settings.cookies_path)
        if self.settings.ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = self.settings.ffmpeg_dir

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
                    ruta_subs=None,
                    bytes_video=bytes_vid,
                    origen="instagram_reels",
                    estado="ok"
                )
            except Exception as e:
                logger.error(f"[Instagram] Error descargando Reel {candidato.video_id}: {e}")
                return None