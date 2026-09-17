# -*- coding: utf-8 -*-
"""Orquestacion: seleccionar -> transcribir -> clasificar -> indexar.

Mismo contrato de eventos que `recopilador.pipeline`: on_progress(tipo, **datos)
con "log", "seleccion", "video" y "fin", de modo que la interfaz y la consola
consumen las dos etapas igual.

Va en serie a proposito. La GPU serializa el trabajo de todas formas, y varios
hilos solo multiplicarian la memoria reservada por la tarjeta.
"""

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .clasificador import ErrorOllama, clasificar
from .config import AjustesAnalisis, MINIMO_PALABRAS
from .models import (Cancelado, EsquemaDataset, ResumenAnalisis, Transcripcion)
from .store import AnalisisStore
from .transcriptor import Transcriptor


def _fila_a_transcripcion(fila) -> Transcripcion:
    """Reconstruye lo justo de una transcripcion ya guardada.

    No se leen los segmentos: para clasificar solo hace falta el texto, y
    traerlos seria pasear miles de filas por nada.
    """
    return Transcripcion(video_id=fila["video_id"],
                         texto=fila["transcripcion"] or "",
                         idioma=fila["idioma"] or "",
                         modelo=fila["modelo_voz"] or "")


def analizar(settings,
             esquema: EsquemaDataset,
             ajustes: Optional[AjustesAnalisis] = None,
             tema: Optional[str] = None,
             solo_pendientes: bool = True,
             on_progress: Optional[Callable[..., None]] = None,
             cancel_event: Optional[threading.Event] = None,
             store: Optional[AnalisisStore] = None) -> ResumenAnalisis:
    """Transcribe y clasifica el corpus ya descargado."""
    inicio = time.time()
    ajustes = ajustes or AjustesAnalisis()
    emitir = on_progress or (lambda *a, **k: None)
    log = lambda m: emitir("log", mensaje=m)

    resumen = ResumenAnalisis()
    store_propio = store is None
    store = store or AnalisisStore(settings.db_path)
    transcriptor = Transcriptor(ajustes, log=log)

    def cancelado() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    try:
        store.guardar_esquema(esquema)

        huerfanas = store.reconciliar()
        if huerfanas:
            log("%d transcripciones eran de vídeos que ya no están en el "
                "índice: se descartan" % huerfanas)

        filas = store.seleccionar(tema=tema, esquema=esquema.nombre,
                                  solo_pendientes=solo_pendientes)
        resumen.seleccionados = len(filas)
        emitir("seleccion", n=len(filas), tema=tema, esquema=esquema.nombre)

        if not filas:
            log("no hay videos que analizar con ese filtro")
        else:
            _recorrer(filas, settings, esquema, ajustes, store, transcriptor,
                      resumen, emitir, log, cancelado, cancel_event)

        resumen.cancelado = cancelado()
        resumen.segundos = time.time() - inicio
        emitir("fin", resumen=resumen)
        return resumen

    finally:
        transcriptor.descargar()
        if store_propio:
            store.cerrar()


def _recorrer(filas, settings, esquema, ajustes, store, transcriptor, resumen,
              emitir, log, cancelado, cancel_event):
    total = len(filas)
    for i, fila in enumerate(filas, 1):
        if cancelado():
            log("cancelado: %d de %d procesados" % (i - 1, total))
            return

        video_id = fila["video_id"]
        titulo = fila["titulo"] or ""
        estado, detalle = "ok", ""

        try:
            trans, nueva = _asegurar_transcripcion(
                fila, settings, ajustes, store, transcriptor, cancel_event)
            if nueva:
                resumen.transcritos += 1
            elif trans is not None:
                resumen.ya_estaban += 1

            if trans is None:
                estado, detalle = "error", "el archivo de vídeo ya no está"
                resumen.errores += 1
            else:
                estado, detalle = _clasificar_si_toca(
                    fila, trans, esquema, ajustes, store, resumen,
                    retranscrito=nueva)

        except Cancelado:
            log("cancelado: %d de %d procesados" % (i - 1, total))
            return
        except ErrorOllama as exc:
            # Si Ollama se ha caido, fallaran todos los siguientes igual: mejor
            # parar y decirlo que llenar la tabla de errores identicos.
            resumen.errores += 1
            emitir("video", video_id=video_id, titulo=titulo, estado="error",
                   detalle=str(exc), hechos=i, objetivo=total)
            log("se detiene el análisis: %s" % exc)
            return
        except Exception as exc:                    # red de seguridad
            resumen.errores += 1
            estado, detalle = "error", str(exc)[:300]

        emitir("video", video_id=video_id, titulo=titulo, estado=estado,
               detalle=detalle, hechos=i, objetivo=total)


def _asegurar_transcripcion(fila, settings, ajustes, store, transcriptor,
                            cancel_event):
    """Devuelve (transcripcion, es_nueva). (None, False) si el video no esta."""
    if fila["transcripcion"] is not None and not ajustes.retranscribir:
        return _fila_a_transcripcion(fila), False

    # Igual que en `Store.reconciliar`: la ruta guardada es absoluta y no
    # resuelve si el corpus se abre desde otro entorno, asi que se cae a la
    # convencion de nombres antes de darlo por ausente.
    plataforma = (fila["plataforma"] if "plataforma" in fila.keys() else None) or "youtube"
    ruta = settings.localizar_video(fila["video_id"], fila["ruta_video"] or "", plataforma)
    if ruta is None:
        # El indice puede ir por delante del disco si se borraron videos a mano;
        # `Store.reconciliar` lo arregla en la siguiente descarga.
        return None, False

    trans = transcriptor.transcribir(fila["video_id"], ruta, cancel=cancel_event)
    store.guardar_transcripcion(trans, con_segmentos=ajustes.guardar_segmentos)
    return trans, True


def _clasificar_si_toca(fila, trans, esquema, ajustes, store, resumen,
                        retranscrito=False):
    """Clasifica si hace falta. Devuelve (estado, detalle) para el evento."""
    if not ajustes.clasificar:
        return "ok", "%d palabras" % trans.n_palabras

    # `retranscrito` fuerza la clasificacion aunque no se haya pedido: la que
    # habia describia un texto que ya no existe, y dejarla seria guardar en el
    # dataset una etiqueta que no corresponde a su propia transcripcion.
    if (fila["campos_json"] is not None and not ajustes.reclasificar
            and not retranscrito):
        return "ok", "ya clasificado"

    clas = clasificar(fila, trans.texto, esquema, ajustes)
    store.guardar_clasificacion(clas)

    resumen.clasificados += 1
    if trans.n_palabras < MINIMO_PALABRAS:
        resumen.sin_habla += 1
    detalle = _resumir_campos(clas.campos)
    if clas.fuente:
        detalle = "%s  <%s>" % (detalle, clas.fuente)
    return ("sin_voz" if trans.n_palabras < MINIMO_PALABRAS else "ok"), detalle


def _resumir_campos(campos) -> str:
    """Una linea corta con lo clasificado, para el log de la ventana."""
    trozos = []
    for k, v in campos.items():
        if v is None or v == [] or v == "":
            continue
        if isinstance(v, list):
            v = "|".join(v)
        elif isinstance(v, bool):
            v = "sí" if v else "no"
        trozos.append("%s=%s" % (k, str(v)[:28]))
    return ", ".join(trozos[:4])
