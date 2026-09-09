# -*- coding: utf-8 -*-
"""Indice SQLite de lo ya recolectado.

Es el punto de enganche para etapas posteriores (transcripcion, analisis):
basta con leer la tabla `videos` para saber que hay en disco y donde.
"""

import io
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import RegistroVideo

ESQUEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id      TEXT,
    plataforma    TEXT DEFAULT 'youtube',
    tema          TEXT,
    titulo        TEXT,
    url           TEXT,
    canal         TEXT,
    duracion      REAL,
    ancho         INTEGER,
    alto          INTEGER,
    vistas        INTEGER,
    fecha_subida  TEXT,
    ruta_video    TEXT,
    ruta_meta     TEXT,
    ruta_subs     TEXT,
    bytes_video   INTEGER,
    descargado_en TEXT,
    origen        TEXT,
    estado        TEXT,
    detalle       TEXT,
    transcrito    INTEGER DEFAULT 0,
    PRIMARY KEY (plataforma, video_id)
);

CREATE INDEX IF NOT EXISTS idx_videos_tema   ON videos(tema);
CREATE INDEX IF NOT EXISTS idx_videos_estado ON videos(estado);
CREATE INDEX IF NOT EXISTS idx_videos_plataforma ON videos(plataforma);

CREATE TABLE IF NOT EXISTS busquedas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tema          TEXT,
    fecha         TEXT,
    backend       TEXT,
    n_pedidos     INTEGER,
    n_candidatos  INTEGER,
    n_descargados INTEGER,
    cancelado     INTEGER
);
"""

_CAMPOS = [
    "video_id", "plataforma", "tema", "titulo", "url", "canal", "duracion", "ancho", "alto",
    "vistas", "fecha_subida", "ruta_video", "ruta_meta", "ruta_subs",
    "bytes_video", "descargado_en", "origen", "estado", "detalle",
]


class Store:
    """Acceso al indice. Seguro para usarse desde varios hilos."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        with self._lock:
            current = self._con.execute("PRAGMA table_info(videos)")
            columnas = [row["name"] for row in current.fetchall()]
            if columnas and "plataforma" not in columnas:
                self._con.executescript("""
                    CREATE TABLE videos_nueva (
                        video_id      TEXT,
                        plataforma    TEXT DEFAULT 'youtube',
                        tema          TEXT,
                        titulo        TEXT,
                        url           TEXT,
                        canal         TEXT,
                        duracion      REAL,
                        ancho         INTEGER,
                        alto          INTEGER,
                        vistas        INTEGER,
                        fecha_subida  TEXT,
                        ruta_video    TEXT,
                        ruta_meta     TEXT,
                        ruta_subs     TEXT,
                        bytes_video   INTEGER,
                        descargado_en TEXT,
                        origen        TEXT,
                        estado        TEXT,
                        detalle       TEXT,
                        transcrito    INTEGER DEFAULT 0,
                        PRIMARY KEY (plataforma, video_id)
                    );
                    INSERT INTO videos_nueva (
                        video_id, plataforma, tema, titulo, url, canal, duracion,
                        ancho, alto, vistas, fecha_subida, ruta_video, ruta_meta,
                        ruta_subs, bytes_video, descargado_en, origen, estado, detalle, transcrito
                    )
                    SELECT 
                        video_id, 'youtube', tema, titulo, url, canal, duracion,
                        ancho, alto, vistas, fecha_subida, ruta_video, ruta_meta,
                        ruta_subs, bytes_video, descargado_en, origen, estado, detalle, transcrito
                    FROM videos;
                    DROP TABLE videos;
                    ALTER TABLE videos_nueva RENAME TO videos;
                """)
            self._con.executescript(ESQUEMA)
            self._con.commit()

    def cerrar(self):
        with self._lock:
            self._con.close()

    # -- consultas -------------------------------------------------------
    def ya_existe(self, video_id: str, plataforma = "youtube") -> bool:
        """True si el video ya se descargo correctamente antes."""
        with self._lock:
            cur = self._con.execute(
                "SELECT estado FROM videos WHERE video_id = ? AND plataforma = ? ", 
                (video_id, plataforma),
            )
            fila = cur.fetchone()
        return fila is not None and fila["estado"] == "ok"

    def ids_conocidos(self, plataforma: Optional[str] = None) -> set:
        """Ids ya descargados con exito.
        
        Si se especifica 'plataforma', devuelve el set de 'video_id'.
        Si no se especifica, devuelve el set de tuplas '(plataforma, video_id)'.
        """
        with self._lock:
            if plataforma:
                current = self._con.execute(
                    "SELECT video_id FROM videos WHERE estado = 'ok' AND plataforma = ?",
                    (plataforma, )
                )
                return set(r["video_id"] for r in current.fetchall())
            else:
                current = self._con.execute(
                    "SELECT plataforma, video_id FROM videos WHERE estado = 'ok'"
                )
                return set((r["plataforma"], r["video_id"]) for r in current.fetchall())

    def listar(self, tema: Optional[str] = None, plataforma: Optional[str] = None, solo_ok: bool = True) -> List[sqlite3.Row]:
        sql = "SELECT * FROM videos"
        cond, params = [], []
        if tema:
            cond.append("tema = ?")
            params.append(tema)
        if plataforma:
            cond.append("plataforma = ?")
            params.append(plataforma)
        if solo_ok:
            cond.append("estado = 'ok'")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY descargado_en DESC"
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    # -- mantenimiento ---------------------------------------------------
    def reconciliar(self, settings):
        """Pone indice, archive.txt y disco de acuerdo.

        Si se vacia `data/videos` a mano, las filas 'ok' y el archive.txt de
        yt-dlp siguen afirmando que esos videos existen: `ids_conocidos` los
        excluye de la busqueda y, si aun asi aparecieran, yt-dlp los saltaria por
        el archivo de control. El corpus quedaria imposible de reconstruir.

        Devuelve (filas_descartadas, ficheros_barridos).
        """
        with self._lock:
            filas = self._con.execute(
                "SELECT video_id, plataforma, ruta_video FROM videos WHERE estado = 'ok'"
            ).fetchall()

        perdidos, vivos, recolocados = [], [], []
        for f in filas:
            # Por la ruta guardada primero y por la convencion de nombres
            # despues: la ruta del indice es absoluta y no resuelve si el corpus
            # se abre desde otro sitio --el contenedor lo ve en /app/data--, y
            # dar por perdido lo que solo esta en otra ruta borraba del indice
            # videos que seguian en disco.
            plat = f["plataforma"] or "youtube"
            vid = f["video_id"]
            real = settings.localizar_video(vid, f["ruta_video"] or "", plataforma = plat)
            if real is None:
                perdidos.append((plat,vid))
                continue
            vivos.append((plat,vid))
            if str(real) != (f["ruta_video"] or ""):
                recolocados.append((str(real), plat, vid))

        if recolocados:
            # Se deja la ruta al dia para que la proxima pasada no tenga que
            # volver a buscar, y para que el analisis abra el fichero correcto.
            with self._lock:
                self._con.executemany(
                    "UPDATE videos SET ruta_video = ? WHERE plataforma = ? AND video_id = ?",
                    [(r[0], r[1], r[2]) for r in recolocados],
                )
                self._con.commit()

        if perdidos:
            with self._lock:
                self._con.executemany(
                    "DELETE FROM videos WHERE plataforma = ? AND video_id = ?",
                    perdidos,
                )
                self._con.commit()

        self._sincronizar_archivo(Path(settings.archive_path), vivos)
        return len(perdidos), self._barrer_huerfanas(settings, set(vivos))

    @staticmethod
    def _barrer_huerfanas(settings, vivos: set) -> int:
        """Borra fichas y subtitulos cuyo video ya no esta en disco.

        Una descarga que falla despues de escribir el .info.json deja la ficha
        suelta; sin barrerla, meta/ acumula entradas que no corresponden a nada
        del corpus y falsean cualquier recuento posterior.
        """
        barridas = 0
        # Convencion esperada de nombres de archivo: "<plataforma>_<id>.*" o "<id>.*"
        vivos_claves = set()
        for plat, vid in vivos:
            vivos_claves.add(vid)
            vivos_claves.add(f"{plat}_{vid}")

        for carpeta in (settings.meta_dir, settings.subs_dir):
            if not carpeta.exists():
                continue
            for resto in carpeta.iterdir():
                if not resto.is_file():
                    continue
                identificador = resto.name.split(".")[0]
                if identificador in vivos_claves:
                    continue
                try:
                    resto.unlink()
                    barridas += 1
                except OSError:
                    pass
        return barridas

    @staticmethod
    def _sincronizar_archivo(ruta: Path, entradas: List[tuple[str, str]]) -> bool:
        """Deja archive.txt con exactamente los ids dados. True si hubo cambios."""
        deseado = [f"{plat} {vid}" for plat, vid in entradas]
        actual = []
        if ruta.exists():
            with io.open(str(ruta), encoding="utf-8") as f:
                actual = [l.strip() for l in f if l.strip()]
        if actual == deseado:
            return False
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with io.open(str(ruta), "w", encoding="utf-8") as f:
            for linea in deseado:
                f.write(linea + "\n")
        return True

    # -- escritura -------------------------------------------------------
    def guardar(self, reg: RegistroVideo):
        d = reg.como_dict()
        if not d.get("plataforma"):
            d["plataforma"] = "youtube"
        if not d.get("descargado_en"):
            d["descargado_en"] = datetime.now().isoformat(timespec="seconds")
        
        valores = [d.get(c) for c in _CAMPOS]
        sql = "INSERT OR REPLACE INTO videos (%s) VALUES (%s)" % (
            ", ".join(_CAMPOS), ", ".join("?" * len(_CAMPOS))
        )
        with self._lock:
            self._con.execute(sql, valores)
            self._con.commit()

    def registrar_busqueda(
        self, tema, backend, n_pedidos, n_candidatos, n_descargados, cancelado=False
    ):
        with self._lock:
            self._con.execute(
                "INSERT INTO busquedas (tema, fecha, backend, n_pedidos, "
                "n_candidatos, n_descargados, cancelado) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    tema,
                    datetime.now().isoformat(timespec="seconds"),
                    backend,
                    n_pedidos,
                    n_candidatos,
                    n_descargados,
                    1 if cancelado else 0,
                ),
            )
            self._con.commit()
