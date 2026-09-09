# -*- coding: utf-8 -*-
"""Orquestacion: buscar -> filtrar -> descargar en paralelo -> indexar."""

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Optional

from .downloader import Cancelado, es_transitorio
from .models import (ESTADO_DESCARTADO, ESTADO_ERROR, ESTADO_OK, ESTADO_OMITIDO,
                     RegistroVideo, Resumen)
from .providers import BaseScraper, obtener_provider
from .store import Store


class _Bandera(object):
    """Une la cancelacion del usuario con la parada interna por objetivo cumplido."""

    def __init__(self, *eventos):
        self._eventos = [e for e in eventos if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._eventos)


def _esperar(segundos, bandera):
    """Pausa troceada para que Cancelar responda al momento. True si se corto."""
    fin = time.time() + segundos
    while time.time() < fin:
        if bandera.is_set():
            return True
        time.sleep(0.25)
    return bandera.is_set()


def _limpiar_parciales(settings):
    for p in settings.videos_dir.glob("*.part"):
        try:
            p.unlink()
        except OSError:
            pass


def recolectar(tema: str,
               n: int,
               settings,
               plataforma: str = "youtube",
               on_progress: Optional[Callable[..., None]] = None,
               cancel_event: Optional[threading.Event] = None,
               store: Optional[Store] = None) -> Resumen:
    """Descarga hasta `n` videos sobre `tema` usando la plataforma indicada.

    on_progress(tipo, **datos) recibe eventos: "log", "busqueda", "video", "fin".
    """
    inicio = time.time()
    emitir = on_progress or (lambda *a, **k: None)
    log = lambda m: emitir("log", mensaje=m)

    settings.preparar()
    store_propio = store is None
    store = store or Store(settings.db_path)

    parar = threading.Event()
    bandera = _Bandera(cancel_event, parar)

    resumen = Resumen(tema=tema, pedidos=n)

    try:
        # Instanciar el proveedor correspondiente (YouTube, TikTok, Instagram)
        provider = obtener_provider(plataforma, settings)

        faltantes, huerfanas = store.reconciliar(settings)
        if faltantes:
            log("%d videos del indice ya no estan en disco: se descartan" % faltantes)
        if huerfanas:
            log("%d fichas o subtitulos sueltos sin video: se borran" % huerfanas)

        # Filtrar IDs conocidos para esta plataforma en especifico
        conocidos = store.ids_conocidos(plataforma) if hasattr(store, "ids_conocidos") else store.ids_conocidos()
        if conocidos:
            log("[%s] %d videos ya en el indice se excluiran de la busqueda" % (plataforma.upper(), len(conocidos)))

        log("[%s] Buscando videos para el tema '%s'..." % (plataforma.upper(), tema))
        candidatos = provider.buscar(tema, n, ids_excluir=conocidos)

        resumen.backend = plataforma
        resumen.candidatos = len(candidatos)
        emitir("busqueda", candidatos=len(candidatos), backend=plataforma, objetivo=n)

        if not candidatos:
            log("la busqueda no devolvio candidatos nuevos")
        elif bandera.is_set():
            resumen.cancelado = True
        else:
            _rondas(provider, candidatos, tema, n, settings, store, resumen,
                    emitir, log, bandera, parar)

        if cancel_event is not None and cancel_event.is_set():
            resumen.cancelado = True

        _limpiar_parciales(settings)
        resumen.segundos = time.time() - inicio
        store.registrar_busqueda(tema, resumen.backend, n, resumen.candidatos,
                                 resumen.descargados, resumen.cancelado)
        emitir("fin", resumen=resumen)
        return resumen

    finally:
        if store_propio:
            store.cerrar()


def _contabilizar(reg: RegistroVideo, resumen: Resumen):
    if reg.estado == ESTADO_OK:
        resumen.descargados += 1
        resumen.bytes += reg.bytes_video or 0
    elif reg.estado == ESTADO_OMITIDO:
        resumen.omitidos += 1
    elif reg.estado == ESTADO_DESCARTADO:
        resumen.descartados += 1
    else:
        resumen.errores += 1


def _rondas(provider: BaseScraper, candidatos, tema, n, settings, store, resumen,
            emitir, log, bandera, parar):
    """Descarga en rondas con reintentos para fallos transitorios."""
    pendientes = list(candidatos)
    for intento in range(settings.reintentos + 1):
        fallidos, sobrantes = _descargar_lote(provider, pendientes, tema, n, settings,
                                              store, resumen, emitir, bandera,
                                              parar, intento)
        pendientes = fallidos + sobrantes
        if not pendientes or resumen.descargados >= n or bandera.is_set():
            break
        if not fallidos:
            continue
        log("%d descargas fallaron de forma temporal; se reintentan en %.0f s"
            % (len(fallidos), settings.pausa_reintento))
        if _esperar(settings.pausa_reintento, bandera):
            break

    if resumen.descargados >= n or bandera.is_set():
        return

    faltan = n - resumen.descargados
    if resumen.errores >= faltan:
        log("Error recurrente en la descarga. Verifica conectividad o limites de la plataforma.")
    else:
        log("Se agotaron los candidatos: %d de %d descargados." % (resumen.descargados, n))


def _descargar_lote(provider: BaseScraper, candidatos, tema, n, settings, store,
                    resumen, emitir, bandera, parar, intento=0):
    """Lanza ejecuciones paralelas de provider.descargar()."""
    pendientes = iter(candidatos)
    en_vuelo = {}
    reintentables = []
    ultima_ronda = intento >= settings.reintentos

    def ejecutar_descarga(candidato):
        if bandera.is_set():
            raise Cancelado()
        
        reg = provider.descargar(candidato)
        if reg is None:
            return RegistroVideo(
                video_id=candidato.video_id,
                plataforma=provider.PLATAFORMA,
                tema=tema,
                url=candidato.url,
                titulo=candidato.titulo,
                estado=ESTADO_ERROR,
                detalle="Error al descargar o procesar con el proveedor"
            )
        
        reg.tema = tema
        return reg

    def procesar(fut):
        cand = en_vuelo.pop(fut)
        try:
            reg = fut.result()
        except Cancelado:
            return
        except Exception as exc:
            reg = RegistroVideo(
                video_id=cand.video_id,
                plataforma=provider.PLATAFORMA,
                tema=tema,
                url=cand.url,
                titulo=cand.titulo,
                estado=ESTADO_ERROR,
                detalle=str(exc)[:300]
            )

        if (reg.estado == ESTADO_ERROR and not ultima_ronda
                and es_transitorio(reg.detalle)):
            reintentables.append(cand)
            emitir("video", registro=reg, hechos=resumen.descargados,
                   objetivo=n, reintentable=True)
            return

        store.guardar(reg)
        _contabilizar(reg, resumen)
        emitir("video", registro=reg, hechos=resumen.descargados, objetivo=n)

    with ThreadPoolExecutor(max_workers=settings.concurrency) as ex:
        def lanzar():
            while (len(en_vuelo) < settings.concurrency
                   and resumen.descargados + len(en_vuelo) < n
                   and not bandera.is_set()):
                try:
                    cand = next(pendientes)
                except StopIteration:
                    return
                en_vuelo[ex.submit(ejecutar_descarga, cand)] = cand

        lanzar()
        while en_vuelo:
            hechos, _ = wait(list(en_vuelo), return_when=FIRST_COMPLETED)
            for fut in hechos:
                procesar(fut)

            if resumen.descargados >= n:
                parar.set()
                break
            if bandera.is_set():
                break
            lanzar()

        while en_vuelo:
            hechos, _ = wait(list(en_vuelo), return_when=FIRST_COMPLETED)
            for fut in hechos:
                procesar(fut)

    return reintentables, list(pendientes)