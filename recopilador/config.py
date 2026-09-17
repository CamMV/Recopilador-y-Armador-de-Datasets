# -*- coding: utf-8 -*-
"""Configuracion global del recopilador."""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

RAIZ = Path(__file__).resolve().parent.parent

# Extensiones que cuentan como video. El merge_output_format es mp4, pero yt-dlp
# puede dejar otra cosa si no hay que unir pistas.
EXT_VIDEO = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv")


def _cargar_env():
    """Lee el .env de la raiz del proyecto si python-dotenv esta disponible."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(RAIZ / ".env")


_cargar_env()


def localizar_ffmpeg() -> Optional[str]:
    """Carpeta que contiene ffmpeg.exe, o None si no se encuentra.

    Se busca tambien fuera del PATH porque winget lo instala sin refrescar la
    variable en las terminales ya abiertas.
    """
    en_path = shutil.which("ffmpeg")
    if en_path:
        return str(Path(en_path).parent)

    candidatos = []
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidatos.extend(
            Path(local).glob("Microsoft/WinGet/Packages/*FFmpeg*/**/bin/ffmpeg.exe"))
    for base in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        candidatos.append(Path(base) / "ffmpeg.exe")

    for c in candidatos:
        if c.exists():
            return str(c.parent)
    return None


def localizar_js_runtime() -> dict:
    """Motores de JavaScript que yt-dlp puede usar, en el formato de `js_runtimes`."""
    runtimes = {}
    for nombre in ("deno", "node", "bun"):
        ruta = shutil.which(nombre)
        if ruta:
            runtimes[nombre] = {"path": ruta}
    return runtimes


@dataclass
class Settings:
    """Parametros de una corrida de recoleccion."""

    data_dir: Path = field(default_factory=lambda: RAIZ / "data")
    max_duration: int = 180         # segundos; tope real de un Short desde 2024
    max_resolucion: int = 720       # lado corto: en vertical la altura es el lado largo
    concurrency: int = 2
    sleep_interval: float = 1.0     # pausa minima entre peticiones
    solo_vertical: bool = True
    subtitulos: bool = True
    idiomas_subs: List[str] = field(default_factory=lambda: ["es", "en"])
    
    # Credenciales y claves de APIs
    youtube_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("YOUTUBE_API_KEY") or None
    )
    instagram_username: Optional[str] = field(
        default_factory=lambda: os.getenv("INSTAGRAM_USERNAME") or None
    )
    instagram_password: Optional[str] = field(
        default_factory=lambda: os.getenv("INSTAGRAM_PASSWORD") or None
    )
    instagram_sessionid: Optional[str] = field(
        default_factory=lambda: os.getenv("INSTAGRAM_SESSIONID") or None
    )
    cookies_path: Optional[Path] = field(
        default_factory=lambda: (RAIZ / "cookies.txt") if (RAIZ / "cookies.txt").exists() else None
    )

    # Navegador automatizado (busqueda en TikTok). TikTok bloquea el modo
    # oculto y pide captcha a los visitantes anonimos, asi que por defecto la
    # ventana se ve: si aparece un reto, lo resuelve la persona.
    navegador_oculto: bool = field(
        default_factory=lambda: os.getenv("RECOPILADOR_NAVEGADOR_OCULTO", "").lower() in ("1", "true", "si")
    )
    espera_captcha: float = 120.0   # segundos que se espera a que se resuelva un reto

    ffmpeg_dir: Optional[str] = field(default_factory=localizar_ffmpeg)
    js_runtimes: dict = field(default_factory=localizar_js_runtime)

    # Reintentos por rafagas de bloqueo
    reintentos: int = 2             # rondas extra sobre los fallos transitorios
    pausa_reintento: float = 25.0   # segundos de espera antes de cada ronda

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.cookies_path:
            self.cookies_path = Path(self.cookies_path)

    # -- rutas derivadas -------------------------------------------------
    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def meta_dir(self) -> Path:
        return self.data_dir / "meta"

    @property
    def subs_dir(self) -> Path:
        return self.data_dir / "subs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    @property
    def archive_path(self) -> Path:
        return self.data_dir / "archive.txt"

    @property
    def sesiones_dir(self) -> Path:
        """Sesiones y cookies por plataforma. Contiene credenciales: no compartir."""
        return self.data_dir / ".sesiones"

    def perfil_navegador(self, plataforma: str) -> Path:
        return self.sesiones_dir / ("navegador_%s" % plataforma)

    def cookies_de(self, plataforma: str) -> Optional[Path]:
        """cookies.txt para yt-dlp: el exportado de la plataforma, o el global."""
        propio = self.sesiones_dir / ("%s_cookies.txt" % plataforma)
        if propio.exists():
            return propio
        return self.cookies_path

    def preparar(self) -> "Settings":
        """Crea el arbol de carpetas de datos. Idempotente."""
        for d in (self.data_dir, self.videos_dir, self.meta_dir, self.subs_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def localizar_video(self, video_id: str,
                        ruta_guardada: str = "",
                        plataforma: str = "youtube") -> Optional[Path]:
        """Ruta real del video, o None si no esta en disco.

        Soporta nombres con prefijo de plataforma (`tiktok_12345.mp4`) y
        mantiene retrocompatibilidad con el formato antiguo sin prefijo (`abc123.mp4`).
        """
        if ruta_guardada:
            ruta = Path(ruta_guardada)
            if ruta.exists():
                return ruta

        # 1. Buscar con convencion de plataforma (ej: instagram_abc123.mp4)
        encontrados_prefijo = [
            p for p in self.videos_dir.glob(f"{plataforma}_{video_id}.*")
            if p.suffix.lower() in EXT_VIDEO
        ]
        if encontrados_prefijo:
            return encontrados_prefijo[0]

        # 2. Fallback sin prefijo (retrocompatibilidad con YouTube). Solo para
        # YouTube: un shortcode de Instagram podria coincidir con un id suyo.
        if plataforma != "youtube":
            return None
        encontrados_legacy = [
            p for p in self.videos_dir.glob(f"{video_id}.*")
            if p.suffix.lower() in EXT_VIDEO
        ]
        return encontrados_legacy[0] if encontrados_legacy else None