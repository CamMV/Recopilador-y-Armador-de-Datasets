# -*- coding: utf-8 -*-
"""Punto de entrada con soporte multiplataforma (YouTube, TikTok, Instagram)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MARCA_RELANZADO = "RECOPILADOR_RELANZADO"


def _python_del_venv(con_consola: bool):
    if os.name == "nt":
        ruta = RAIZ / ".venv" / "Scripts" / ("python.exe" if con_consola else "pythonw.exe")
    else:
        ruta = RAIZ / ".venv" / "bin" / "python"
    return ruta if ruta.exists() else None


def _relanzar_en_venv(argv):
    try:
        ya_esta = Path(sys.prefix).resolve() == (RAIZ / ".venv").resolve()
    except OSError:
        ya_esta = True
    if ya_esta or os.environ.get(MARCA_RELANZADO):
        return

    py = _python_del_venv(con_consola=bool(argv))
    if py is None:
        return

    os.environ[MARCA_RELANZADO] = "1"
    orden = [str(py), str(RAIZ / "main.py")] + list(argv)
    try:
        if argv:
            sys.exit(subprocess.call(orden))
        subprocess.Popen(orden, close_fds=True)
        sys.exit(0)
    except OSError:
        os.environ.pop(MARCA_RELANZADO, None)


from recopilador.config import Settings
from recopilador.models import ESTADO_OK
from analisis.config import MODELO_WHISPER_POR_DEFECTO


def _cli(args):
    from recopilador import entorno, pipeline

    fallos = entorno.problemas()
    if fallos:
        print("No se puede descargar con este entorno:")
        for f in fallos:
            print("\n- %s" % f)
        return 1

    settings = Settings(
        data_dir=Path(args.carpeta) if args.carpeta else (RAIZ / "data"),
        max_duration=args.max_dur,
        concurrency=args.hilos,
        solo_vertical=not args.incluir_horizontales,
        subtitulos=not args.sin_subs,
    )

    def on_progress(tipo, **datos):
        if tipo == "log":
            print("[info] %s" % datos.get("mensaje", ""))
        elif tipo == "busqueda":
            print("[busqueda] %d candidatos (%s)"
                  % (datos.get("candidatos", 0), datos.get("backend", "?")))
        elif tipo == "video":
            reg = datos["registro"]
            marca = "ok " if reg.estado == ESTADO_OK else reg.estado[:3]
            cola = " -> se reintentara" if datos.get("reintentable") else ""
            print("[%3d/%d] %s %s  %s%s"
                  % (datos.get("hechos", 0), datos.get("objetivo", 0), marca,
                     reg.video_id, (reg.titulo or reg.detalle)[:60], cola))

    resumen = pipeline.recolectar(
        args.tema,
        args.n,
        settings,
        plataforma=args.plataforma,
        on_progress=on_progress
    )
    print("\n[%s] Descargados %d/%d | omitidos %d | descartados %d | errores %d | %.1f MB | %.0f s"
          % (args.plataforma.upper(), resumen.descargados, resumen.pedidos, resumen.omitidos,
             resumen.descartados, resumen.errores, resumen.mb, resumen.segundos))
    return 0 if resumen.descargados else 1


def _cargar_esquema(store, nombre):
    from analisis.models import esquema_por_defecto
    guardado = store.cargar_esquema(nombre)
    if guardado is not None:
        return guardado
    esquema = esquema_por_defecto()
    esquema.nombre = nombre
    print("[info] esquema '%s': %s" % (nombre, ", ".join(c.nombre for c in esquema.columnas)))
    return esquema


def _analizar(args):
    from analisis import entorno as entorno_analisis
    from analisis import exportador, pipeline as pipeline_analisis
    from analisis.config import AjustesAnalisis
    from analisis.store import AnalisisStore

    settings = Settings(data_dir=Path(args.carpeta) if args.carpeta else (RAIZ / "data"))
    ajustes = AjustesAnalisis(
        modelo_whisper=args.modelo_voz,
        idioma=args.idioma,
        clasificar=not args.sin_clasificar,
        retranscribir=args.retranscribir,
        reclasificar=args.reclasificar,
    )
    if args.modelo_llm:
        ajustes.modelo_llm = args.modelo_llm

    for aviso in entorno_analisis.avisos(ajustes):
        print("[aviso] %s\n" % aviso)
    fallos = entorno_analisis.problemas(ajustes, clasificar=ajustes.clasificar)
    if fallos:
        print("No se puede analizar con este entorno:")
        for f in fallos:
            print("\n- %s" % f)
        return 1

    store = AnalisisStore(settings.db_path)
    try:
        esquema = _cargar_esquema(store, args.esquema)

        def on_progress(tipo, **datos):
            if tipo == "log":
                print("[info] %s" % datos.get("mensaje", ""))
            elif tipo == "seleccion":
                print("[seleccion] %d videos" % datos.get("n", 0))
            elif tipo == "video":
                print("[%3d/%d] %s %s  %s  [%s]"
                      % (datos.get("hechos", 0), datos.get("objetivo", 0),
                         datos.get("estado", "")[:3], datos.get("video_id", ""),
                         (datos.get("titulo") or "")[:50],
                         datos.get("detalle", "")[:60]))

        resumen = pipeline_analisis.analizar(
            settings, esquema, ajustes, tema=args.tema,
            solo_pendientes=not args.todos, on_progress=on_progress, store=store)

        print("\n%d transcritos | %d clasificados | %d errores | %.0f s"
              % (resumen.transcritos, resumen.clasificados, resumen.errores, resumen.segundos))

        if args.exportar:
            ruta, n = exportador.exportar(settings, esquema, tema=args.tema, store=store)
            print("CSV: %s (%d filas)" % (ruta, n))
        return 0 if not resumen.errores else 1
    finally:
        store.cerrar()


def _exportar_csv(args):
    from analisis import exportador
    from analisis.store import AnalisisStore

    settings = Settings(data_dir=Path(args.carpeta) if args.carpeta else (RAIZ / "data"))
    store = AnalisisStore(settings.db_path)
    try:
        esquema = _cargar_esquema(store, args.esquema)
        ruta, n = exportador.exportar(settings, esquema, tema=args.tema, store=store)
        print("CSV: %s (%d filas)" % (ruta, n))
        return 0 if n else 1
    finally:
        store.cerrar()


def _consola_utf8():
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    _relanzar_en_venv(argv)
    _consola_utf8()

    p = argparse.ArgumentParser(description="Recopilador Multi-plataforma de Videos Cortos")
    p.add_argument("--cli", action="store_true", help="ejecutar sin interfaz gráfica")
    p.add_argument("--plataforma", "-p", default="youtube", choices=["youtube", "tiktok", "instagram"],
                   help="plataforma de origen (youtube, tiktok, instagram)")
    p.add_argument("--tema", help="temática a buscar")
    p.add_argument("--n", type=int, default=10, help="cantidad de videos a descargar")
    p.add_argument("--max-dur", type=int, default=180, help="duración máxima en segundos")
    p.add_argument("--hilos", type=int, default=3, help="descargas simultáneas")
    p.add_argument("--carpeta", help="carpeta de datos")
    p.add_argument("--sin-subs", action="store_true", help="no descargar subtítulos")
    p.add_argument("--incluir-horizontales", action="store_true", help="incluir videos horizontales")

    g = p.add_argument_group("análisis (transcripción y dataset)")
    g.add_argument("--analizar", action="store_true", help="transcribir y clasificar lo ya descargado")
    g.add_argument("--exportar-csv", action="store_true", help="volcar a CSV lo ya analizado")
    g.add_argument("--esquema", default="general", help="esquema de columnas")
    g.add_argument("--todos", action="store_true", help="procesar todo el corpus")
    g.add_argument("--sin-clasificar", action="store_true", help="solo transcribir")
    g.add_argument("--retranscribir", action="store_true", help="retranscribir")
    g.add_argument("--reclasificar", action="store_true", help="reclasificar")
    g.add_argument("--modelo-voz", default=MODELO_WHISPER_POR_DEFECTO, help="modelo Whisper")
    g.add_argument("--modelo-llm", help="modelo de Ollama")
    g.add_argument("--idioma", help="idioma del audio")
    g.add_argument("--exportar", action="store_true", help="exportar CSV")

    args = p.parse_args(argv)

    if args.cli:
        if not args.tema:
            p.error("--cli requiere --tema")
        return _cli(args)

    if args.analizar:
        return _analizar(args)

    if args.exportar_csv:
        return _exportar_csv(args)

    from recopilador.ui.app import lanzar
    lanzar()
    return 0


if __name__ == "__main__":
    sys.exit(main())