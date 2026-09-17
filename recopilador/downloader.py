# -*- coding: utf-8 -*-
"""Descarga de un video corto con yt-dlp: video + metadatos + subtitulos.

Sirve para YouTube, TikTok e Instagram: lo que cambia entre plataformas es el
nombre de los ficheros, el selector de formato y las cookies.

Se hace en dos fases a proposito. El video y su ficha van primero; los
subtitulos automaticos van despues, en una pasada aparte cuyos fallos se
ignoran. YouTube limita con HTTP 429 el endpoint de subtitulos con facilidad,
y en una sola fase ese 429 abortaba tambien la descarga del video.
"""

import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import EXT_VIDEO
from .models import (Candidato, RegistroVideo, ESTADO_DESCARTADO, ESTADO_ERROR,
                     ESTADO_OK, ESTADO_OMITIDO, nombre_base)

# Claves voluminosas de la ficha que no aportan al analisis posterior: sin
# ellas el .info.json baja de ~900 KB a unos pocos KB por video.
CLAVES_PESADAS = ("formats", "thumbnails", "automatic_captions", "subtitles",
                  "heatmap", "requested_formats", "requested_downloads",
                  "http_headers", "_format_sort_fields")


# Fallos que no son del video sino del momento: YouTube limitando el ritmo o
# rechazando el cliente de reproduccion. El mismo id suele bajar sin problema
# minutos despues, asi que el pipeline los reintenta en vez de darlos por
# perdidos y devolver una corrida con cero descargas.
SENALES_TRANSITORIAS = (
    "the page needs to be reloaded",
    "http error 403",
    "http error 429",
    "too many requests",
    "sign in to confirm",
    "please try again later",
    "unable to download api page",
    "unable to download video data",
    "failed to extract any player response",
    "read timed out",
    "timed out",
    "connection reset",
    "rate-limit",
    "rate limit",
    "please wait a few minutes",
    "http error 5",
    "unable to extract universal data",     # TikTok cortando por ritmo
)

# Clientes de reproduccion para las rondas de reintento. Si el que YouTube esta
# rechazando es el de por defecto, pedir otro suele bastar. No se usan en la
# primera ronda: cada cliente extra es una peticion mas, y el problema de fondo
# es justamente el exceso de peticiones.
CLIENTES_REINTENTO = (
    ["default", "tv"],
    ["default", "android_vr", "tv"],
)


def es_transitorio(detalle: str) -> bool:
    """True si el fallo parece del momento y merece otra oportunidad."""
    texto = (detalle or "").lower()
    return any(senal in texto for senal in SENALES_TRANSITORIAS)


class _SinVoz(object):
    """Silencia a yt-dlp en la fase de subtitulos, cuyos fallos ya se ignoran."""

    def debug(self, _mensaje):
        pass

    info = warning = error = debug


class Cancelado(Exception):
    """Se pidio cancelar la corrida mientras se descargaba."""


class _Filtro(object):
    """Rechaza el video ANTES de bajarlo, mirando la ficha ya extraida.

    Evita gastar ancho de banda en lo que luego se iba a descartar (videos
    demasiado largos, u horizontales cuando se piden solo verticales).
    """

    def __init__(self, settings, cancel_event=None):
        self.settings = settings
        self.cancel_event = cancel_event
        self.motivo = None

    def __call__(self, info, incomplete=False, **_kwargs):
        # Se ejecuta durante la extraccion, antes de bajar nada: es el punto
        # mas temprano donde se puede atender una cancelacion.
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise Cancelado()
        self.motivo = motivo_descarte(self.settings, info.get("duration"),
                                      info.get("width"), info.get("height"))
        return self.motivo


def motivo_descarte(settings, duracion, ancho, alto) -> Optional[str]:
    """Por que un video no entra en el corpus, o None si entra."""
    if duracion is not None and duracion > settings.max_duration:
        return "dura %ds (maximo %ds)" % (int(duracion), settings.max_duration)
    if settings.solo_vertical and ancho and alto and ancho >= alto:
        return "horizontal (%sx%s)" % (ancho, alto)
    return None


def _comunes(settings, cancel_event, plataforma="youtube") -> dict:
    def hook(_d):
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelado()

    # La plantilla "%(id)s" pasa por el mismo prefijo que nombre_base(), asi
    # los ficheros de yt-dlp y los de la descarga directa se llaman igual.
    base = nombre_base(plataforma, "%(id)s")
    opciones = {
        "outtmpl": {
            "default": str(settings.videos_dir / (base + ".%(ext)s")),
            "infojson": str(settings.meta_dir / (base + ".%(ext)s")),
            "subtitle": str(settings.subs_dir / (base + ".%(ext)s")),
        },
        "sleep_interval_requests": 0.75,
        "retries": 3,
        "fragment_retries": 3,
        "ignoreerrors": False,      # queremos el mensaje de error, no un None mudo
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "consoletitle": False,
        "no_color": True,           # sin codigos ANSI dentro de reg.detalle
        "progress_hooks": [hook],
    }
    if settings.ffmpeg_dir:
        opciones["ffmpeg_location"] = settings.ffmpeg_dir
    # Se omite la clave si no hay ninguno: pasar {} le quitaria a yt-dlp su
    # deno por defecto en vez de dejarlo como esta.
    if getattr(settings, "js_runtimes", None):
        opciones["js_runtimes"] = dict(settings.js_runtimes)
    cookies = settings.cookies_de(plataforma)
    if cookies:
        opciones["cookiefile"] = str(cookies)
    return opciones


def _formato(settings, plataforma) -> str:
    if plataforma != "youtube":
        # TikTok e Instagram sirven mp4 ya mezclados, en H.264 y en H.265
        # (bytevc1). Se prefiere H.264 por compatibilidad; Instagram ademas
        # ofrece pistas DASH separadas, que solo se usan si no hay otra cosa.
        fmt = ("best[ext=mp4][vcodec^=h264]/best[ext=mp4][vcodec^=avc1]/"
               "best[ext=mp4]/best")
        if settings.ffmpeg_dir:
            fmt += "/bestvideo[vcodec^=avc1]+bestaudio/bestvideo+bestaudio"
        return fmt
    if settings.ffmpeg_dir:
        # Con ffmpeg se pueden mezclar pistas separadas, que dan mas calidad
        # que el mp4 ya mezclado (suele venir solo en 360p). Se piden H.264 y
        # AAC antes que VP9/AV1+Opus: dentro de un .mp4 estos ultimos abren mal
        # en muchos reproductores y librerias de vision por computador.
        return ("bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "best[ext=mp4]/bestvideo+bestaudio/best")
    return "best[ext=mp4]/best"


def _opciones_video(settings, cancel_event, filtro, intento=0,
                    plataforma="youtube") -> dict:
    # El limite se expresa sobre el LADO CORTO: en un Short vertical la altura
    # es el lado largo (1280, 1920), asi que filtrar por height<=720 dejaba
    # fuera todo salvo los 360x640. El campo `res` de yt-dlp es justamente el
    # menor de ancho y alto, y se aplica ordenando, no filtrando.
    opciones = _comunes(settings, cancel_event, plataforma)
    opciones.update({
        "format": _formato(settings, plataforma),
        "format_sort": ["res:%d" % settings.max_resolucion],
        "merge_output_format": "mp4",
        "match_filter": filtro,
        "writeinfojson": True,
        "download_archive": str(settings.archive_path),
        "sleep_interval": settings.sleep_interval,
        "max_sleep_interval": max(settings.sleep_interval, 3.0),
    })
    if intento and plataforma == "youtube":
        clientes = CLIENTES_REINTENTO[min(intento, len(CLIENTES_REINTENTO)) - 1]
        opciones["extractor_args"] = {"youtube": {"player_client": list(clientes)}}
    return opciones


def _idiomas_subs(settings, plataforma) -> list:
    idiomas = list(settings.idiomas_subs)
    if plataforma == "tiktok":
        # TikTok etiqueta con ISO 639-2 y region: "spa-ES", "eng-US".
        tres = {"es": "spa", "en": "eng", "pt": "por", "fr": "fra"}
        idiomas = ["%s.*" % i for i in idiomas] + \
                  ["%s.*" % tres[i] for i in settings.idiomas_subs if i in tres]
    return idiomas


def _opciones_subs(settings, cancel_event, plataforma="youtube") -> dict:
    opciones = _comunes(settings, cancel_event, plataforma)
    opciones.update({
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _idiomas_subs(settings, plataforma),
        "subtitlesformat": "vtt",
        "sleep_interval_subtitles": 1,
        "logger": _SinVoz(),        # el 429 de subtitulos no debe ensuciar la salida
    })
    return opciones


def _limpiar_restos(base: str, settings):
    """Borra lo que quedo a medias de un video abortado."""
    for carpeta in (settings.videos_dir, settings.meta_dir, settings.subs_dir):
        for resto in carpeta.glob("%s.*" % base):
            try:
                resto.unlink()
            except OSError:
                pass


def _ruta_video(ydl, info, settings, plataforma="youtube") -> Optional[Path]:
    """Ubica el archivo realmente escrito en disco."""
    for req in (info.get("requested_downloads") or []):
        ruta = req.get("filepath") or req.get("_filename")
        if ruta and Path(ruta).exists() and Path(ruta).suffix.lower() in EXT_VIDEO:
            return Path(ruta)
    try:
        ruta = Path(ydl.prepare_filename(info))
        if ruta.exists() and ruta.suffix.lower() in EXT_VIDEO:
            return ruta
    except Exception:
        pass
    return settings.localizar_video(info.get("id", ""), plataforma=plataforma)


def _aligerar_ficha(ruta_meta: Path):
    """Quita del .info.json las listas gigantes de formatos y miniaturas."""
    try:
        with io.open(str(ruta_meta), encoding="utf-8") as f:
            ficha = json.load(f)
        for clave in CLAVES_PESADAS:
            ficha.pop(clave, None)
        with io.open(str(ruta_meta), "w", encoding="utf-8") as f:
            json.dump(ficha, f, ensure_ascii=False, indent=1)
    except Exception:
        pass                        # dejar la ficha completa tampoco es un problema


def _bajar_subs(cand: Candidato, settings, cancel_event) -> str:
    """Segunda fase, opcional y tolerante a fallos."""
    from yt_dlp import YoutubeDL

    base = cand.nombre_base
    try:
        with YoutubeDL(_opciones_subs(settings, cancel_event, cand.plataforma)) as ydl:
            ydl.extract_info(cand.url, download=True)
    except Cancelado:
        raise
    except Exception:
        pass                        # 429 y similares: los subtitulos son opcionales

    # yt-dlp los deja junto al video cuando no descarga video: se recolocan.
    for suelto in settings.videos_dir.glob("%s.*" % base):
        if suelto.suffix.lower() in EXT_VIDEO:
            continue
        try:
            suelto.replace(settings.subs_dir / suelto.name)
        except OSError:
            pass
    return ";".join(sorted(str(p) for p in settings.subs_dir.glob("%s.*" % base)))


def registro_base(cand: Candidato, tema: str) -> RegistroVideo:
    """Registro inicial con lo que la busqueda ya sabe del candidato."""
    return RegistroVideo(
        video_id=cand.video_id, plataforma=cand.plataforma, tema=tema,
        titulo=cand.titulo, url=cand.url, canal=cand.canal,
        duracion=cand.duracion, ancho=cand.ancho, alto=cand.alto,
        vistas=cand.vistas, fecha_subida=cand.fecha_subida, origen=cand.origen,
        descargado_en=datetime.now().isoformat(timespec="seconds"),
    )


def descargar(cand: Candidato, tema: str, settings, cancel_event=None,
              intento: int = 0) -> RegistroVideo:
    """Baja un candidato. Solo lanza si se cancela; el resto de fallos vuelven
    dentro del registro.

    `intento` es el numero de ronda: a partir de 1 se piden clientes de
    reproduccion alternativos.
    """
    from yt_dlp import YoutubeDL

    reg = registro_base(cand, tema)
    plataforma = cand.plataforma
    base = cand.nombre_base
    filtro = _Filtro(settings, cancel_event)

    # -- fase 1: video + ficha -------------------------------------------
    try:
        opciones = _opciones_video(settings, cancel_event, filtro, intento, plataforma)
        with YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(cand.url, download=True)

            if filtro.motivo:
                reg.estado = ESTADO_DESCARTADO
                reg.detalle = filtro.motivo
                return reg
            if not info:
                reg.estado = ESTADO_OMITIDO
                reg.detalle = "sin informacion (ya en el archivo o no disponible)"
                return reg

            reg.titulo = info.get("title") or reg.titulo
            reg.canal = info.get("channel") or info.get("uploader") or reg.canal
            reg.duracion = info.get("duration") or reg.duracion
            reg.ancho = info.get("width")
            reg.alto = info.get("height")
            reg.vistas = info.get("view_count") or reg.vistas
            reg.fecha_subida = info.get("upload_date") or reg.fecha_subida

            ruta = _ruta_video(ydl, info, settings, plataforma)

    except Cancelado:
        _limpiar_restos(base, settings)
        raise
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            _limpiar_restos(base, settings)
            raise Cancelado()
        # La ficha .info.json se escribe antes de bajar el video: si la descarga
        # falla hay que barrerla, o queda huerfana en meta/ sin su .mp4. Con las
        # rondas de reintento esto se acumularia.
        _limpiar_restos(base, settings)
        reg.estado = ESTADO_ERROR
        reg.detalle = str(exc).replace("\n", " ")[:300]
        return reg

    if ruta is None:
        reg.estado = ESTADO_OMITIDO
        reg.detalle = "ya estaba descargado (archive.txt)"
        return reg

    meta = settings.meta_dir / ("%s.info.json" % base)
    if meta.exists():
        _aligerar_ficha(meta)
        reg.ruta_meta = str(meta)

    reg.ruta_video = str(ruta)
    reg.bytes_video = os.path.getsize(ruta)
    reg.estado = ESTADO_OK

    # -- fase 2: subtitulos, sin que su fallo invalide el video -----------
    # Instagram no publica subtitulos: la pasada solo gastaria peticiones.
    if settings.subtitulos and plataforma != "instagram":
        try:
            reg.ruta_subs = _bajar_subs(cand, settings, cancel_event)
        except Cancelado:
            # El video ya esta completo en disco: se conserva y se indexa, o
            # quedaria huerfano fuera del indice.
            reg.detalle = "cancelado antes de los subtitulos"

    return reg
