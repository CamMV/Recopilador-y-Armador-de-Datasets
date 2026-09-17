# -*- coding: utf-8 -*-
"""Volcado del corpus analizado a un CSV.

El aplanado de las columnas del usuario ocurre aqui y solo aqui: en la base los
valores viven como JSON (`clasificaciones.campos_json`), de modo que cambiar el
esquema no obliga a tocar la estructura de la tabla ni invalida los datasets
anteriores.
"""

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .models import EsquemaDataset
from .store import AnalisisStore

# Metadatos del video que acompanan a cada fila, en este orden.
COLUMNAS_BASE = ["video_id", "plataforma", "url", "tema", "titulo", "canal", "duracion",
                 "ancho", "alto", "vistas", "fecha_subida", "descargado_en"]

COLUMNAS_VOZ = ["idioma", "prob_idioma", "n_palabras", "transcripcion"]

# `fuente_etiquetas` dice en qué se apoyó el modelo para esa fila: el habla, el
# título con sus hashtags, o ambos. Al admitir las dos evidencias, sin esta
# columna no habría forma de separar lo que se oye de lo que el autor escribió.
COLUMNAS_TRAZA = ["fuente_etiquetas", "modelo_voz", "modelo_llm",
                  "clasificado_en", "error"]

_ESPACIOS = re.compile(r"\s+")


def _nombre_archivo(esquema: str, tema: Optional[str]) -> str:
    partes = ["dataset", re.sub(r"[^\w-]+", "_", esquema)]
    if tema:
        partes.append(re.sub(r"[^\w-]+", "_", tema)[:40])
    partes.append(datetime.now().strftime("%Y%m%d-%H%M"))
    return "_".join(p for p in partes if p) + ".csv"


def exportar(settings, esquema: EsquemaDataset, destino: Optional[Path] = None,
             tema: Optional[str] = None, solo_clasificados: bool = False,
             store: Optional[AnalisisStore] = None) -> Tuple[Path, int]:
    """Escribe el CSV y devuelve (ruta, filas escritas)."""
    store_propio = store is None
    store = store or AnalisisStore(settings.db_path)
    try:
        filas = store.filas_dataset(esquema.nombre, tema=tema,
                                    solo_clasificados=solo_clasificados)
        ruta = Path(destino) if destino else (
            Path(settings.data_dir) / _nombre_archivo(esquema.nombre, tema))
        ruta.parent.mkdir(parents=True, exist_ok=True)

        cabecera = (COLUMNAS_BASE + COLUMNAS_VOZ
                    + [c.nombre for c in esquema.columnas] + COLUMNAS_TRAZA)

        # utf-8-sig y no utf-8: sin el BOM, Excel en Windows abre el CSV con los
        # acentos rotos, y es la primera cosa que se hace con este fichero.
        with io.open(str(ruta), "w", encoding="utf-8-sig", newline="") as f:
            escritor = csv.writer(f)
            escritor.writerow(cabecera)
            for fila in filas:
                escritor.writerow(_fila_csv(fila, esquema))

        return ruta, len(filas)
    finally:
        if store_propio:
            store.cerrar()


def _fila_csv(fila, esquema: EsquemaDataset) -> list:
    campos = {}
    if fila["campos_json"]:
        try:
            campos = json.loads(fila["campos_json"])
        except ValueError:
            campos = {}

    # Los saltos de linea se colapsan: el CSV quedaria valido igual gracias al
    # entrecomillado, pero muchas herramientas (y la vista previa de Excel) los
    # leen como filas nuevas. El texto integro sigue en SQLite, con sus tiempos.
    texto = _ESPACIOS.sub(" ", fila["texto"] or "").strip()

    valores = [fila["video_id"], fila["plataforma"] or "youtube", fila["url"], fila["tema"], fila["titulo"],
               fila["canal"], fila["duracion"], fila["ancho"], fila["alto"],
               fila["vistas"], fila["fecha_subida"], fila["descargado_en"],
               fila["idioma"] or "",
               "" if fila["prob_idioma"] is None else "%.3f" % fila["prob_idioma"],
               fila["n_palabras"] if fila["n_palabras"] is not None else "",
               texto]

    valores.extend(c.para_csv(campos.get(c.nombre)) for c in esquema.columnas)
    valores.extend([fila["fuente"] or "", fila["modelo_voz"] or "",
                    fila["modelo_llm"] or "", fila["clasificado_en"] or "",
                    fila["error"] or ""])
    return valores
