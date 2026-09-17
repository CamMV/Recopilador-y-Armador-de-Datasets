# -*- coding: utf-8 -*-
"""Tablas del analisis, sobre el mismo index.sqlite del recopilador.

Se comparte fichero a proposito: el corpus y lo que se deduce de el no deben
poder separarse ni quedar desincronizados, y asi el JOIN entre metadatos,
transcripcion y clasificacion es una consulta y no un cruce a mano.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from recopilador.store import ESQUEMA as ESQUEMA_RECOPILADOR, migrar_videos

from .models import Clasificacion, EsquemaDataset, Transcripcion

ESQUEMA = """
CREATE TABLE IF NOT EXISTS transcripciones (
    video_id       TEXT PRIMARY KEY,
    texto          TEXT,
    idioma         TEXT,
    prob_idioma    REAL,
    n_segmentos    INTEGER,
    n_palabras     INTEGER,
    duracion_audio REAL,
    modelo         TEXT,
    segundos       REAL,
    creado_en      TEXT
);

CREATE TABLE IF NOT EXISTS segmentos (
    video_id TEXT,
    i        INTEGER,
    inicio   REAL,
    fin      REAL,
    texto    TEXT,
    PRIMARY KEY (video_id, i)
);

CREATE TABLE IF NOT EXISTS esquemas (
    nombre          TEXT PRIMARY KEY,
    definicion_json TEXT,
    creado_en       TEXT
);

CREATE TABLE IF NOT EXISTS clasificaciones (
    video_id    TEXT,
    esquema     TEXT,
    campos_json TEXT,
    modelo      TEXT,
    fuente      TEXT,
    error       TEXT,
    creado_en   TEXT,
    PRIMARY KEY (video_id, esquema)
);

CREATE INDEX IF NOT EXISTS idx_clas_esquema ON clasificaciones(esquema);
"""


class AnalisisStore:
    """Acceso a las tablas de analisis. Seguro para usarse desde varios hilos."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            # Las dos pestanas pueden estar trabajando a la vez sobre este mismo
            # fichero. WAL deja que una lea mientras la otra escribe, y el
            # busy_timeout evita que una escritura simultanea muera al instante
            # con "database is locked".
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA busy_timeout=5000")
            # Las tablas del recopilador tambien, por si se analiza antes de
            # haber descargado nunca: aqui se consulta `videos`, y sin esto la
            # primera consulta moriria con "no such table".
            migrar_videos(self._con)
            self._con.executescript(ESQUEMA_RECOPILADOR)
            self._con.executescript(ESQUEMA)
            self._migrar()
            self._con.commit()

    def _migrar(self):
        """Añade columnas nuevas a bases creadas por versiones anteriores.

        `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, asi que
        sin esto una base de antes se quedaria sin `fuente` y toda escritura
        fallaria con "no such column".
        """
        columnas = {f[1] for f in
                    self._con.execute("PRAGMA table_info(clasificaciones)")}
        if "fuente" not in columnas:
            self._con.execute("ALTER TABLE clasificaciones ADD COLUMN fuente TEXT")

    def cerrar(self):
        with self._lock:
            self._con.close()

    # -- mantenimiento ----------------------------------------------------
    def reconciliar(self) -> int:
        """Borra transcripciones y clasificaciones de videos que ya no existen.

        `recopilador.store.Store.reconciliar` quita del indice las filas cuyo
        .mp4 desaparecio del disco, pero no sabe nada de estas tablas. Sin este
        barrido, vaciar `data/videos` deja aqui el analisis de un corpus que ya
        no esta: `conteos` diria "20 transcritos" con diez videos en disco, que
        es justo el recuento falseado que el recopilador se molesta en evitar.

        Devuelve cuantas transcripciones se descartaron.
        """
        with self._lock:
            cur = self._con.execute(
                "DELETE FROM transcripciones WHERE video_id NOT IN "
                "(SELECT video_id FROM videos)")
            borradas = cur.rowcount or 0
            self._con.execute(
                "DELETE FROM segmentos WHERE video_id NOT IN "
                "(SELECT video_id FROM videos)")
            self._con.execute(
                "DELETE FROM clasificaciones WHERE video_id NOT IN "
                "(SELECT video_id FROM videos)")
            self._con.commit()
        return borradas

    # -- seleccion de trabajo --------------------------------------------
    def temas(self) -> List[str]:
        with self._lock:
            filas = self._con.execute(
                "SELECT DISTINCT tema FROM videos WHERE estado = 'ok' "
                "AND tema IS NOT NULL AND tema <> '' ORDER BY tema").fetchall()
        return [f["tema"] for f in filas]

    def seleccionar(self, tema: Optional[str] = None,
                    esquema: Optional[str] = None,
                    solo_pendientes: bool = False) -> List[sqlite3.Row]:
        """Videos descargados sobre los que trabajar, con lo que ya se sabe de ellos.

        `solo_pendientes` deja fuera los que ya tienen transcripcion y, si se da
        un esquema, tambien clasificacion con ese esquema: es lo que permite
        ampliar el corpus y analizar solo lo nuevo.
        """
        sql = ("SELECT v.*, t.texto AS transcripcion, t.idioma, t.modelo AS modelo_voz, "
               "       c.campos_json, c.error AS error_clasificacion "
               "FROM videos v "
               "LEFT JOIN transcripciones t ON t.video_id = v.video_id "
               "LEFT JOIN clasificaciones c "
               "       ON c.video_id = v.video_id AND c.esquema = ? "
               "WHERE v.estado = 'ok'")
        params = [esquema or ""]
        if tema:
            sql += " AND v.tema = ?"
            params.append(tema)
        if solo_pendientes:
            sql += " AND (t.video_id IS NULL"
            if esquema:
                sql += " OR c.video_id IS NULL"
            sql += ")"
        sql += " ORDER BY v.descargado_en DESC"
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def transcripcion(self, video_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._con.execute(
                "SELECT * FROM transcripciones WHERE video_id = ?",
                (video_id,)).fetchone()

    # -- escritura --------------------------------------------------------
    def guardar_transcripcion(self, t: Transcripcion, con_segmentos: bool = True):
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO transcripciones (video_id, texto, idioma, "
                "prob_idioma, n_segmentos, n_palabras, duracion_audio, modelo, "
                "segundos, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.video_id, t.texto, t.idioma, t.prob_idioma, t.n_segmentos,
                 t.n_palabras, t.duracion_audio, t.modelo, t.segundos, ahora))
            self._con.execute("DELETE FROM segmentos WHERE video_id = ?",
                              (t.video_id,))
            if con_segmentos and t.segmentos:
                self._con.executemany(
                    "INSERT INTO segmentos (video_id, i, inicio, fin, texto) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(t.video_id, i, ini, fin, txt)
                     for i, (ini, fin, txt) in enumerate(t.segmentos)])
            # La columna ya existia en el esquema del recopilador, reservada
            # justo para esto.
            self._con.execute("UPDATE videos SET transcrito = 1 WHERE video_id = ?",
                              (t.video_id,))
            self._con.commit()

    def guardar_clasificacion(self, c: Clasificacion):
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO clasificaciones (video_id, esquema, "
                "campos_json, modelo, fuente, error, creado_en) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c.video_id, c.esquema,
                 json.dumps(c.campos, ensure_ascii=False) if c.campos else None,
                 c.modelo, c.fuente or None, c.error or None,
                 datetime.now().isoformat(timespec="seconds")))
            self._con.commit()

    # -- esquemas ---------------------------------------------------------
    def guardar_esquema(self, esquema: EsquemaDataset):
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO esquemas (nombre, definicion_json, "
                "creado_en) VALUES (?, ?, ?)",
                (esquema.nombre, esquema.como_json(),
                 datetime.now().isoformat(timespec="seconds")))
            self._con.commit()

    def cargar_esquema(self, nombre: str) -> Optional[EsquemaDataset]:
        with self._lock:
            fila = self._con.execute(
                "SELECT definicion_json FROM esquemas WHERE nombre = ?",
                (nombre,)).fetchone()
        if not fila:
            return None
        try:
            return EsquemaDataset.desde_json(fila["definicion_json"])
        except (ValueError, TypeError):
            return None

    def nombres_de_esquema(self) -> List[str]:
        with self._lock:
            filas = self._con.execute(
                "SELECT nombre FROM esquemas ORDER BY nombre").fetchall()
        return [f["nombre"] for f in filas]

    def borrar_esquema(self, nombre: str):
        """Quita el esquema y las clasificaciones hechas con el.

        Dejar las clasificaciones huerfanas seria peor: apareceria en el CSV una
        columna que ya nadie sabe definir.
        """
        with self._lock:
            self._con.execute("DELETE FROM clasificaciones WHERE esquema = ?",
                              (nombre,))
            self._con.execute("DELETE FROM esquemas WHERE nombre = ?", (nombre,))
            self._con.commit()

    # -- lectura para el CSV ----------------------------------------------
    def filas_dataset(self, esquema: str, tema: Optional[str] = None,
                      solo_clasificados: bool = False) -> List[sqlite3.Row]:
        sql = ("SELECT v.video_id, v.plataforma, v.tema, v.titulo, v.url, v.canal, v.duracion, "
               "       v.ancho, v.alto, v.vistas, v.fecha_subida, v.descargado_en, "
               "       t.texto, t.idioma, t.prob_idioma, t.n_palabras, "
               "       t.modelo AS modelo_voz, "
               "       c.campos_json, c.modelo AS modelo_llm, c.fuente, "
               "       c.error, c.creado_en AS clasificado_en "
               "FROM videos v "
               "LEFT JOIN transcripciones t ON t.video_id = v.video_id "
               "LEFT JOIN clasificaciones c "
               "       ON c.video_id = v.video_id AND c.esquema = ? "
               "WHERE v.estado = 'ok'")
        params = [esquema]
        if tema:
            sql += " AND v.tema = ?"
            params.append(tema)
        if solo_clasificados:
            sql += " AND c.campos_json IS NOT NULL"
        sql += " ORDER BY v.tema, v.descargado_en DESC"
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def conteos(self, esquema: str) -> Dict[str, int]:
        """Cifras para la interfaz: cuanto hay hecho y cuanto queda."""
        with self._lock:
            fila = self._con.execute(
                "SELECT (SELECT count(*) FROM videos WHERE estado='ok') AS videos, "
                "       (SELECT count(*) FROM transcripciones) AS transcritos, "
                "       (SELECT count(*) FROM clasificaciones "
                "         WHERE esquema = ? AND campos_json IS NOT NULL) AS clasificados",
                (esquema,)).fetchone()
        return {"videos": fila["videos"], "transcritos": fila["transcritos"],
                "clasificados": fila["clasificados"]}
