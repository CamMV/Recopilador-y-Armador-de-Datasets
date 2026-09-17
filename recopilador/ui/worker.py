# -*- coding: utf-8 -*-
"""Hilo trabajador: ejecuta el pipeline y publica eventos en una cola.

Tkinter solo puede tocarse desde el hilo principal, asi que aqui no se importa
nada de tkinter: la UI drena la cola con root.after().
"""

import queue
import threading
import traceback

from .. import pipeline


class Trabajador(object):
    def __init__(self):
        self.cola = queue.Queue()
        self.cancel = threading.Event()
        self._hilo = None

    # -- control ---------------------------------------------------------
    def activo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def iniciar(self, tema, n, settings, plataforma: str = "youtube"):
        if self.activo():
            raise RuntimeError("ya hay una recoleccion en curso")
        self.cancel = threading.Event()
        self.cola = queue.Queue()
        self._hilo = threading.Thread(
            target=self._correr, args=(tema, n, settings, plataforma), daemon=True)
        self._hilo.start()

    def iniciar_login(self, plataforma, settings):
        if self.activo():
            raise RuntimeError("ya hay una tarea en curso")
        self.cancel = threading.Event()
        self.cola = queue.Queue()
        self._hilo = threading.Thread(
            target=self._correr_login, args=(plataforma, settings), daemon=True)
        self._hilo.start()

    def cancelar(self):
        self.cancel.set()

    # -- cuerpo del hilo -------------------------------------------------
    def _correr(self, tema, n, settings, plataforma):
        def on_progress(tipo, **datos):
            self.cola.put((tipo, datos))

        try:
            pipeline.recolectar(tema, n, settings,
                                plataforma=plataforma,
                                on_progress=on_progress,
                                cancel_event=self.cancel)
        except Exception as exc:
            self.cola.put(("error", {
                "mensaje": str(exc),
                "traza": traceback.format_exc(),
            }))
        finally:
            self.cola.put(("terminado", {}))

    def _correr_login(self, plataforma, settings):
        from ..providers.navegador import iniciar_sesion

        try:
            iniciar_sesion(settings, plataforma,
                           log=lambda m: self.cola.put(("log", {"mensaje": m})))
        except Exception as exc:
            self.cola.put(("error", {
                "mensaje": str(exc),
                "traza": traceback.format_exc(),
            }))
        finally:
            self.cola.put(("terminado", {}))

    # -- consumo desde la UI ---------------------------------------------
    def drenar(self, maximo=200):
        """Devuelve los eventos disponibles sin bloquear."""
        eventos = []
        for _ in range(maximo):
            try:
                eventos.append(self.cola.get_nowait())
            except queue.Empty:
                break
        return eventos