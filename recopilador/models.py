# -*- coding: utf-8 -*-
"""Estructuras de datos compartidas por busqueda, descarga y almacenamiento."""

from dataclasses import dataclass, field, asdict
from typing import Optional

ESTADO_OK = "ok"
ESTADO_OMITIDO = "omitido"          # ya estaba en el archivo/indice
ESTADO_DESCARTADO = "descartado"    # bajado pero no cumple filtros (p.ej. horizontal)
ESTADO_ERROR = "error"


@dataclass
class Candidato:
    """Un video encontrado en la busqueda, aun sin descargar."""

    video_id: str
    plataforma: str = "youtube"
    titulo: str = ""
    url: str = ""
    duracion: Optional[float] = None
    canal: str = ""
    origen: str = "ytdlp"           # "ytdlp" | "api" | "navegador" | "instagrapi"
    ancho: Optional[int] = None
    alto: Optional[int] = None
    vistas: Optional[int] = None
    fecha_subida: str = ""
    # Datos propios de la plataforma que la busqueda ya trae y la descarga
    # aprovecha (p.ej. la URL directa del mp4 de Instagram).
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.url and self.plataforma == "youtube":
            self.url = "https://www.youtube.com/watch?v=%s" % self.video_id

    @property
    def nombre_base(self) -> str:
        """Nombre de fichero sin extension.

        YouTube conserva el nombre historico `<id>` para no romper el corpus ya
        descargado; el resto lleva prefijo, porque un shortcode de Instagram
        tiene el mismo alfabeto y longitud que un id de YouTube.
        """
        return nombre_base(self.plataforma, self.video_id)


def nombre_base(plataforma: str, video_id: str) -> str:
    if not plataforma or plataforma == "youtube":
        return video_id
    return "%s_%s" % (plataforma, video_id)


@dataclass
class RegistroVideo:
    """Resultado de intentar descargar un candidato."""

    video_id: str
    plataforma: str = "youtube"
    tema: str = ""
    titulo: str = ""
    url: str = ""
    canal: str = ""
    duracion: Optional[float] = None
    ancho: Optional[int] = None
    alto: Optional[int] = None
    vistas: Optional[int] = None
    fecha_subida: str = ""
    ruta_video: str = ""
    ruta_meta: str = ""
    ruta_subs: str = ""
    bytes_video: int = 0
    descargado_en: str = ""
    origen: str = "ytdlp"
    estado: str = ESTADO_OK
    detalle: str = ""               # mensaje de error o motivo del descarte

    @property
    def exitoso(self) -> bool:
        return self.estado == ESTADO_OK

    def como_dict(self) -> dict:
        return asdict(self)


@dataclass
class Resumen:
    """Cifras finales de una corrida."""

    tema: str = ""
    pedidos: int = 0
    candidatos: int = 0
    descargados: int = 0
    omitidos: int = 0
    descartados: int = 0
    errores: int = 0
    bytes: int = 0
    segundos: float = 0.0
    cancelado: bool = False
    backend: str = ""

    @property
    def mb(self) -> float:
        return self.bytes / (1024.0 * 1024.0)
