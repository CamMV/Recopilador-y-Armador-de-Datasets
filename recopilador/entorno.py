# -*- coding: utf-8 -*-
"""Comprobaciones del entorno de ejecucion.

La app arranca y busca igual de bien en un Python viejo, pero YouTube rechaza el
yt-dlp que se puede instalar ahi y la corrida termina en cero descargas sin decir
por que. Lo mismo pasa sin ffmpeg: el unico formato que queda es el 18, que
YouTube ya bloquea. Estas comprobaciones convierten esos fallos mudos en un
mensaje concreto y accionable.
"""

import sys

PYTHON_MINIMO = (3, 10)
YTDLP_MINIMO = (2026, 1, 1)

RECETA = ("Abre la app con recopilador.bat, que usa el entorno del proyecto. Si el "
          "entorno no existe, crealo con:\n"
          "    py -3.11 -m venv .venv\n"
          "    .venv\\Scripts\\python.exe -m pip install -r requirements.txt")


def _tupla(version: str) -> tuple:
    """Pasa "2026.08.19" a (2026, 8, 19). Tupla vacia si no se puede leer."""
    partes = []
    for trozo in (version or "").split(".")[:3]:
        try:
            partes.append(int(trozo))
        except ValueError:
            return ()
    return tuple(partes)


def version_ytdlp():
    """Version de yt-dlp instalada, o None si no esta."""
    try:
        from yt_dlp import version
    except ImportError:
        return None
    return getattr(version, "__version__", "")


def problemas(settings=None) -> list:
    """Fallos del entorno que impiden descargar, en lenguaje llano.

    Lista vacia = todo en orden.
    """
    fallos = []

    if sys.version_info < PYTHON_MINIMO:
        fallos.append(
            "Este Python es %d.%d y hacen falta %d.%d o superior.\n"
            "El yt-dlp mas nuevo instalable aqui es de 2025, y YouTube lo rechaza "
            "con \"The page needs to be reloaded\": la busqueda encuentra videos "
            "pero no se descarga ninguno.\n"
            "Interprete en uso: %s\n%s"
            % (sys.version_info[0], sys.version_info[1],
               PYTHON_MINIMO[0], PYTHON_MINIMO[1], sys.executable, RECETA))

    v = version_ytdlp()
    if v is None:
        fallos.append("Falta yt-dlp en este Python (%s).\n%s"
                      % (sys.executable, RECETA))
    elif _tupla(v) and _tupla(v) < YTDLP_MINIMO:
        fallos.append(
            "yt-dlp %s esta desfasado: YouTube cambia su proteccion cada pocas "
            "semanas y las versiones viejas dejan de poder descargar.\n"
            "Actualizalo con:\n"
            "    .venv\\Scripts\\python.exe -m pip install -U yt-dlp" % v)

    if settings is None:
        try:
            from .config import Settings
            settings = Settings()
        except Exception:               # no dejar que un fallo aqui tape el resto
            settings = None
    if settings is not None and not settings.ffmpeg_dir:
        fallos.append(
            "No se encuentra ffmpeg. YouTube entrega los Shorts con video y audio "
            "en pistas separadas; sin ffmpeg para unirlas solo queda el formato 18, "
            "que YouTube bloquea, y no se descarga nada.\n"
            "Instalalo con:\n"
            "    winget install --id Gyan.FFmpeg -e --scope user")

    return fallos
