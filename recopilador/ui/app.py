# -*- coding: utf-8 -*-
"""Pestaña «Recopilar»: buscar y descargar videos por plataforma."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..config import RAIZ, Settings
from ..models import ESTADO_DESCARTADO, ESTADO_ERROR, ESTADO_OK, ESTADO_OMITIDO
from .comun import PADDING, PanelBase, abrir_carpeta
from .worker import Trabajador

MAX_CANTIDAD = 500


class App(PanelBase):
    def __init__(self, master):
        PanelBase.__init__(self, master, padding=PADDING)

        self.trabajador = Trabajador()
        self.objetivo = 0

        self.var_plataforma = tk.StringVar(value="youtube")
        self.var_tema = tk.StringVar()
        self.var_cantidad = tk.StringVar(value="20")
        self.var_duracion = tk.StringVar(value="180")
        self.var_hilos = tk.StringVar(value="3")
        self.var_carpeta = tk.StringVar(value=str(RAIZ / "data"))
        self.var_subs = tk.BooleanVar(value=True)
        self.var_vertical = tk.BooleanVar(value=True)
        self.var_estado = tk.StringVar(value="Listo.")

        self._construir()
        self._arrancar_drenaje()

    def _construir(self):
        self.columnconfigure(1, weight=1)
        fila = 0

        # Seleccion de Plataforma
        ttk.Label(self, text="Plataforma:").grid(row=fila, column=0, sticky="w")
        cb_plat = ttk.Combobox(
            self,
            textvariable=self.var_plataforma,
            values=["youtube", "tiktok", "instagram"],
            state="readonly",
            width=15
        )
        cb_plat.grid(row=fila, column=1, sticky="w", pady=2)
        fila += 1

        # Tema
        ttk.Label(self, text="Tema:").grid(row=fila, column=0, sticky="w")
        e_tema = ttk.Entry(self, textvariable=self.var_tema)
        e_tema.grid(row=fila, column=1, columnspan=3, sticky="ew", pady=2)
        e_tema.focus_set()
        e_tema.bind("<Return>", lambda _e: self._iniciar())
        fila += 1

        # Parametros de descarga
        params = ttk.Frame(self)
        params.grid(row=fila, column=0, columnspan=4, sticky="ew", pady=(6, 2))
        ttk.Label(params, text="Cantidad:").pack(side="left")
        ttk.Entry(params, textvariable=self.var_cantidad, width=6).pack(side="left", padx=(4, 12))
        ttk.Label(params, text="Duración máx (s):").pack(side="left")
        ttk.Entry(params, textvariable=self.var_duracion, width=6).pack(side="left", padx=(4, 12))
        ttk.Label(params, text="Descargas simultáneas:").pack(side="left")
        ttk.Entry(params, textvariable=self.var_hilos, width=4).pack(side="left", padx=4)
        fila += 1

        # Carpeta
        ttk.Label(self, text="Carpeta:").grid(row=fila, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.var_carpeta).grid(row=fila, column=1, columnspan=2, sticky="ew", pady=2)
        ttk.Button(self, text="Elegir...", command=self._elegir_carpeta).grid(row=fila, column=3, sticky="e", padx=(4, 0))
        fila += 1

        # Opciones
        opts = ttk.Frame(self)
        opts.grid(row=fila, column=0, columnspan=4, sticky="w", pady=(6, 2))
        ttk.Checkbutton(opts, text="Descargar subtítulos automáticos", variable=self.var_subs).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(opts, text="Solo videos verticales", variable=self.var_vertical).pack(side="left")
        fila += 1

        # Botones de accion
        acciones = ttk.Frame(self)
        acciones.grid(row=fila, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        self.btn_iniciar = ttk.Button(acciones, text="Buscar y descargar", command=self._iniciar)
        self.btn_iniciar.pack(side="left")
        self.btn_cancelar = ttk.Button(acciones, text="Cancelar", command=self._cancelar, state="disabled")
        self.btn_cancelar.pack(side="left", padx=6)
        ttk.Button(acciones, text="Abrir carpeta", command=self._abrir_carpeta).pack(side="left")
        ttk.Label(acciones, textvariable=self.var_estado).pack(side="right")
        fila += 1

        self.barra = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.barra.grid(row=fila, column=0, columnspan=4, sticky="ew", pady=4)
        fila += 1

        self._crear_log(fila)

    def _elegir_carpeta(self):
        elegida = filedialog.askdirectory(initialdir=self.var_carpeta.get() or str(RAIZ))
        if elegida:
            self.var_carpeta.set(elegida)

    def _abrir_carpeta(self):
        abrir_carpeta(self.var_carpeta.get())

    def avisar_del_entorno(self):
        from ..entorno import problemas
        fallos = problemas()
        if not fallos:
            return
        self.btn_iniciar.configure(state="disabled")
        self.var_estado.set("Entorno incorrecto.")
        self._escribir("La descarga no puede funcionar con este entorno:", "err")
        for f in fallos:
            self._escribir("  - %s" % f, "err")
        messagebox.showerror("Entorno incorrecto", "\n\n".join(fallos))

    def _iniciar(self):
        if self.trabajador.activo():
            return
        tema = self.var_tema.get().strip()
        plataforma = self.var_plataforma.get().strip()
        if not tema:
            messagebox.showwarning("Falta el tema", "Escribe una temática para buscar.")
            return
        try:
            cantidad = self._entero(self.var_cantidad, "La cantidad", 1, MAX_CANTIDAD)
            duracion = self._entero(self.var_duracion, "La duración máxima", 5, 600)
            hilos = self._entero(self.var_hilos, "Las descargas simultáneas", 1, 8)
        except ValueError as exc:
            messagebox.showwarning("Dato inválido", str(exc))
            return

        settings = Settings(
            data_dir=Path(self.var_carpeta.get().strip() or (RAIZ / "data")),
            max_duration=duracion,
            concurrency=hilos,
            solo_vertical=self.var_vertical.get(),
            subtitulos=self.var_subs.get(),
        )

        self.objetivo = cantidad
        self.barra.configure(maximum=cantidad, value=0)
        self.var_estado.set("Buscando en %s..." % plataforma.title())
        self.btn_iniciar.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self._escribir("=== [%s] %s | %d videos, hasta %d s, %d en paralelo ==="
                       % (plataforma.upper(), tema, cantidad, duracion, hilos))
        
        # Pasa el parametro plataforma al hilo secundario
        self.trabajador.iniciar(tema, cantidad, settings, plataforma=plataforma)

    def _cancelar(self):
        if self.trabajador.activo():
            self.trabajador.cancelar()
            self.var_estado.set("Cancelando...")
            self.btn_cancelar.configure(state="disabled")
            self._escribir("Cancelación solicitada; esperando descargas en curso.", "warn")

    def _evento(self, tipo, datos):
        if tipo == "log":
            self._escribir(datos.get("mensaje", ""))
        elif tipo == "busqueda":
            self._escribir("Búsqueda terminada (%s): %d candidatos."
                           % (datos.get("backend", "?"), datos.get("candidatos", 0)))
            self.var_estado.set("Descargando 0/%d" % self.objetivo)
        elif tipo == "video":
            reg = datos["registro"]
            hechos = min(datos.get("hechos", 0), self.objetivo)
            self.barra.configure(value=hechos)
            self.var_estado.set("Descargando %d/%d" % (hechos, self.objetivo))
            texto, tag = self._linea_video(reg)
            if datos.get("reintentable"):
                texto, tag = texto + "  -> se reintentará", "warn"
            self._escribir(texto, tag)
        elif tipo == "error":
            self._escribir("Fallo inesperado: %s" % datos.get("mensaje", ""), "err")
            self._escribir(datos.get("traza", ""), "err")
        elif tipo == "fin":
            r = datos["resumen"]
            self._escribir(
                "Resumen: %d descargados, %d omitidos, %d descartados, %d errores; %.1f MB en %.0f s%s"
                % (r.descargados, r.omitidos, r.descartados, r.errores, r.mb,
                   r.segundos, " (cancelado)" if r.cancelado else ""),
                "warn" if r.cancelado else "ok")
        elif tipo == "terminado":
            self.btn_iniciar.configure(state="normal")
            self.btn_cancelar.configure(state="disabled")
            self.var_estado.set("Listo.")

    def _linea_video(self, reg):
        marca, tag = {
            ESTADO_OK: ("ok ", "ok"),
            ESTADO_OMITIDO: ("omi", "info"),
            ESTADO_DESCARTADO: ("des", "warn"),
            ESTADO_ERROR: ("err", "err"),
        }.get(reg.estado, ("???", "info"))
        titulo = (reg.titulo or "")[:70]
        extra = "%.1f MB" % ((reg.bytes_video or 0) / (1024.0 * 1024.0)) if reg.estado == ESTADO_OK else reg.detalle
        return ("%s %s  %s  [%s]" % (marca, reg.video_id, titulo, extra), tag)


def lanzar():
    root = tk.Tk()
    root.title("Recopilador Multi-Plataforma y Analizador de Videos")
    root.geometry("900x730")
    root.minsize(700, 540)

    cuaderno = ttk.Notebook(root)
    cuaderno.pack(fill="both", expand=True)

    recopilar = App(cuaderno)
    cuaderno.add(recopilar, text="  Recopilar  ")
    root.after(50, recopilar.avisar_del_entorno)

    try:
        from analisis.ui.panel import PanelAnalisis
        analizar = PanelAnalisis(cuaderno)
        cuaderno.add(analizar, text="  Analizar  ")
        root.after(80, analizar.avisar_del_entorno)

        def _al_cambiar_pestana(_evento):
            if cuaderno.select() == str(analizar):
                analizar.al_mostrarse()

        cuaderno.bind("<<NotebookTabChanged>>", _al_cambiar_pestana)
    except Exception as exc:
        aviso = ttk.Frame(cuaderno, padding=PADDING)
        ttk.Label(aviso, text="No se pudo cargar la pestaña de análisis: %s" % exc).pack(anchor="w")
        cuaderno.add(aviso, text="  Analizar  ")

    root.mainloop()