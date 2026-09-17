# -*- coding: utf-8 -*-
"""Instagram Reels: busqueda con instagrapi y descarga directa o con yt-dlp.

Instagram no deja buscar ni descargar sin sesion: yt-dlp responde "empty media
response" y la pagina de hashtag exige login. La sesion se obtiene, por orden:

1. La sesion de instagrapi guardada de una corrida anterior.
2. La cookie `sessionid`: INSTAGRAM_SESSIONID en el .env, o la exportada por
   `python main.py --login instagram` (inicio de sesion en navegador; no hace
   falta escribir la contrasena en ningun fichero).
3. INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD en el .env.

Usa una cuenta secundaria: Instagram restringe o bloquea las cuentas que hacen
muchas peticiones automatizadas.
"""

import io
import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from .. import downloader
from ..downloader import Cancelado, motivo_descarte, registro_base
from ..models import ESTADO_DESCARTADO, ESTADO_ERROR, ESTADO_OK, Candidato, RegistroVideo
from .base import BaseScraper, Log, hashtags
from .navegador import UA, sessionid_guardado

ARCHIVO_SESION = "instagram_instagrapi.json"
MAX_HASHTAGS = 4
PAGINAS_BUSQUEDA = 3
TROZO = 256 * 1024


def _es_video(m) -> bool:
    return getattr(m, "media_type", None) == 2 and bool(getattr(m, "code", None))


def candidato_de_media(m) -> Candidato:
    dim = getattr(m, "dimensions", None)
    usuario = getattr(m, "user", None)
    tomada = getattr(m, "taken_at", None)
    return Candidato(
        video_id=m.code,
        plataforma="instagram",
        titulo=(m.caption_text or "").strip(),
        url="https://www.instagram.com/reel/%s/" % m.code,
        duracion=m.video_duration or None,
        canal=getattr(usuario, "username", "") or "",
        origen="instagrapi",
        ancho=getattr(dim, "width", None),
        alto=getattr(dim, "height", None),
        vistas=m.play_count or m.view_count or None,
        fecha_subida=tomada.strftime("%Y%m%d") if tomada else "",
        extra={
            "video_url": str(m.video_url) if m.video_url else "",
            "pk": str(m.pk),
            "product_type": m.product_type or "",
            "likes": m.like_count,
            "comentarios": m.comment_count,
        },
    )


def _fijar_dimensiones(crudo: dict):
    """instagrapi descarta original_width/height; se pasan a `dimensions`."""
    if crudo.get("dimensions"):
        return
    ancho, alto = crudo.get("original_width"), crudo.get("original_height")
    if not (ancho and alto) and crudo.get("video_versions"):
        mayor = max(crudo["video_versions"],
                    key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))
        ancho, alto = mayor.get("width"), mayor.get("height")
    if ancho and alto:
        crudo["dimensions"] = {"width": ancho, "height": alto}


def _medias_en(payload) -> list:
    """Diccionarios con pinta de media dentro de una respuesta cruda."""
    encontrados = []
    pila = [payload]
    while pila:
        nodo = pila.pop()
        if isinstance(nodo, dict):
            if "code" in nodo and "media_type" in nodo and "pk" in nodo:
                encontrados.append(nodo)
                continue
            pila.extend(nodo.values())
        elif isinstance(nodo, list):
            pila.extend(nodo)
    return encontrados


class InstagramScraper(BaseScraper):
    PLATAFORMA = "instagram"

    def __init__(self, settings):
        super().__init__(settings)
        self._cl = None

    # -- sesion -----------------------------------------------------------
    def _cliente(self, log: Log):
        if self._cl is not None:
            return self._cl
        try:
            from instagrapi import Client
        except ImportError:
            log("[Instagram] falta instagrapi: pip install instagrapi")
            return None

        s = self.settings
        ruta = s.sesiones_dir / ARCHIVO_SESION
        cl = Client()
        cl.delay_range = [1, 3]         # pausa entre peticiones, como una persona
        sessionid = s.instagram_sessionid or sessionid_guardado(s, self.PLATAFORMA)

        try:
            if ruta.exists():
                cl.load_settings(ruta)
            if s.instagram_username and s.instagram_password:
                # Con la sesion cargada, login() la reutiliza en vez de iniciar
                # otra (cada inicio nuevo aumenta el riesgo de bloqueo).
                cl.login(s.instagram_username, s.instagram_password)
            elif sessionid:
                cl.login_by_sessionid(sessionid)
            elif not ruta.exists():
                log("[Instagram] no hay sesion. Opciones:\n"
                    "   - python main.py --login instagram   (recomendado)\n"
                    "   - INSTAGRAM_SESSIONID=... en el .env\n"
                    "   - INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD en el .env")
                return None
            s.sesiones_dir.mkdir(parents=True, exist_ok=True)
            cl.dump_settings(ruta)
        except Exception as exc:
            log("[Instagram] no se pudo iniciar sesion: %s: %s"
                % (type(exc).__name__, str(exc)[:200]))
            if "challenge" in type(exc).__name__.lower():
                log("[Instagram] Instagram pide verificar la cuenta: abre la app o "
                    "la web, confirma que eras tu y vuelve a intentarlo.")
            return None

        self._cl = cl
        return cl

    # -- busqueda ---------------------------------------------------------
    def buscar(self, tema: str, limite: int, ids_excluir: Set[str],
               log: Optional[Log] = None, cancel_event=None) -> List[Candidato]:
        log = log or (lambda _m: None)
        cancelado = lambda: cancel_event is not None and cancel_event.is_set()
        self.backend = "instagrapi"
        cl = self._cliente(log)
        if cl is None:
            return []

        objetivo = max(limite * 2, limite + 10)
        excluir = set(ids_excluir or ())
        vistos: Set[str] = set()
        cands: List[Candidato] = []
        descartados = [0]

        def agregar(medias, fuente):
            nuevos = 0
            for m in medias:
                if len(cands) >= objetivo:
                    break
                if not _es_video(m) or m.code in vistos or m.code in excluir:
                    continue
                vistos.add(m.code)
                c = candidato_de_media(m)
                if motivo_descarte(self.settings, c.duracion, c.ancho, c.alto):
                    descartados[0] += 1
                    continue
                cands.append(c)
                nuevos += 1
            log("[Instagram] %s -> %d reels (total %d)" % (fuente, nuevos, len(cands)))

        # 1) Busqueda por texto en la pestana Reels: la mas tematica.
        try:
            self._buscar_texto(cl, tema, objetivo, agregar, cancelado)
        except Exception as exc:
            log("[Instagram] busqueda de texto no disponible: %s" % str(exc)[:120])

        # 2) Hashtags: pestana de reels y, si falla, la de destacados.
        for tag in hashtags(tema)[:MAX_HASHTAGS]:
            if len(cands) >= objetivo or cancelado():
                break
            try:
                medias = cl.hashtag_medias_reels_v1(tag, amount=objetivo)
            except Exception as exc:
                log("[Instagram] #%s (reels): %s" % (tag, str(exc)[:100]))
                try:
                    medias = cl.hashtag_medias_top(tag, amount=objetivo)
                except Exception as exc2:
                    log("[Instagram] #%s: %s" % (tag, str(exc2)[:100]))
                    continue
            agregar(medias, "#%s" % tag)

        if descartados[0]:
            log("[Instagram] %d reels descartados antes de bajar (duracion u orientacion)"
                % descartados[0])
        cl.dump_settings(self.settings.sesiones_dir / ARCHIVO_SESION)
        return cands

    @staticmethod
    def _buscar_texto(cl, tema, objetivo, agregar, cancelado):
        from instagrapi.extractors import extract_media_v1

        cursor = None
        for _ in range(PAGINAS_BUSQUEDA):
            if cancelado():
                return
            datos = cl.fbsearch_reels_v2(tema, reels_max_id=cursor)
            medias = []
            for crudo in _medias_en(datos):
                _fijar_dimensiones(crudo)
                try:
                    medias.append(extract_media_v1(crudo))
                except Exception:
                    continue
            agregar(medias, "busqueda '%s'" % tema)
            cursor = (datos or {}).get("reels_max_id") or \
                ((datos or {}).get("paging_info") or {}).get("max_id")
            if not cursor or not (datos or {}).get("has_more", True):
                return

    # -- descarga ---------------------------------------------------------
    def descargar(self, candidato: Candidato, tema: str, cancel_event=None,
                  intento: int = 0) -> RegistroVideo:
        url = (candidato.extra or {}).get("video_url")
        fallo_directo = ""
        if url and intento == 0:
            reg = self._descargar_directo(candidato, tema, url, cancel_event)
            if reg.estado != ESTADO_ERROR:
                return reg
            # La URL del CDN caduca a las pocas horas: se prueba con yt-dlp.
            fallo_directo = "descarga directa: %s; " % reg.detalle[:120]
        if not self.settings.cookies_de(self.PLATAFORMA):
            reg = registro_base(candidato, tema)
            reg.estado = ESTADO_ERROR
            reg.detalle = (fallo_directo + "sin cookies para yt-dlp: "
                           "ejecuta python main.py --login instagram")
            return reg
        return downloader.descargar(candidato, tema, self.settings,
                                    cancel_event=cancel_event, intento=intento)

    def _descargar_directo(self, cand: Candidato, tema: str, url: str,
                           cancel_event) -> RegistroVideo:
        s = self.settings
        reg = registro_base(cand, tema)
        base = cand.nombre_base
        destino = s.videos_dir / (base + ".mp4")
        parcial = s.videos_dir / (base + ".mp4.part")

        try:
            peticion = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.instagram.com/"})
            with urllib.request.urlopen(peticion, timeout=60) as resp, \
                    open(str(parcial), "wb") as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise Cancelado()
                    trozo = resp.read(TROZO)
                    if not trozo:
                        break
                    f.write(trozo)
            parcial.replace(destino)
        except Cancelado:
            _borrar(parcial)
            raise
        except Exception as exc:
            _borrar(parcial)
            reg.estado = ESTADO_ERROR
            reg.detalle = str(exc).replace("\n", " ")[:300]
            return reg

        # La busqueda no siempre trae dimensiones: se leen del fichero.
        if not (reg.ancho and reg.alto):
            reg.ancho, reg.alto = _dimensiones(destino, s.ffmpeg_dir)
        motivo = motivo_descarte(s, reg.duracion, reg.ancho, reg.alto)
        if motivo:
            _borrar(destino)
            reg.estado = ESTADO_DESCARTADO
            reg.detalle = motivo
            return reg

        reg.ruta_video = str(destino)
        reg.bytes_video = os.path.getsize(str(destino))
        reg.ruta_meta = str(self._escribir_ficha(cand, reg))
        reg.estado = ESTADO_OK
        return reg

    def _escribir_ficha(self, cand: Candidato, reg: RegistroVideo) -> Path:
        """Ficha con las mismas claves que el .info.json de yt-dlp."""
        extra = cand.extra or {}
        ficha = {
            "id": cand.video_id,
            "extractor_key": "Instagram",
            "plataforma": self.PLATAFORMA,
            "title": reg.titulo,
            "description": reg.titulo,
            "uploader": reg.canal,
            "channel": reg.canal,
            "duration": reg.duracion,
            "width": reg.ancho,
            "height": reg.alto,
            "view_count": reg.vistas,
            "like_count": extra.get("likes"),
            "comment_count": extra.get("comentarios"),
            "upload_date": reg.fecha_subida,
            "webpage_url": cand.url,
            "media_pk": extra.get("pk"),
            "product_type": extra.get("product_type"),
            "_descargado_con": "instagrapi",
            "_fecha": datetime.now().isoformat(timespec="seconds"),
        }
        ruta = self.settings.meta_dir / ("%s.info.json" % cand.nombre_base)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with io.open(str(ruta), "w", encoding="utf-8") as f:
            json.dump(ficha, f, ensure_ascii=False, indent=1)
        return ruta

    def cerrar(self):
        self._cl = None


def _borrar(ruta: Path):
    try:
        ruta.unlink()
    except OSError:
        pass


def _dimensiones(ruta: Path, ffmpeg_dir: Optional[str]):
    """(ancho, alto) del video, o (None, None) si no hay con que leerlo."""
    try:
        import av                       # llega con faster-whisper
        with av.open(str(ruta)) as contenedor:
            pista = contenedor.streams.video[0]
            return pista.width, pista.height
    except Exception:
        pass
    ffprobe = None
    if ffmpeg_dir:
        for nombre in ("ffprobe", "ffprobe.exe"):
            candidato = Path(ffmpeg_dir) / nombre
            if candidato.exists():
                ffprobe = str(candidato)
                break
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        return None, None
    try:
        salida = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(ruta)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        ancho, alto = salida.splitlines()[0].split("x")[:2]
        return int(ancho), int(alto)
    except Exception:
        return None, None
