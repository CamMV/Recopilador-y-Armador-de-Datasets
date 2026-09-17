# -*- coding: utf-8 -*-
"""TikTok: busqueda con navegador (Playwright) y descarga con yt-dlp.

yt-dlp descarga bien un video suelto de TikTok, pero no sirve para buscar: su
extractor de hashtags esta marcado como roto y el de usuarios falla sin
sesion. La web, en cambio, pide a su propia API los resultados de busqueda y de
hashtag mientras se hace scroll; aqui se abre esa web y se leen esas respuestas.
Asi se obtienen tambien duracion, dimensiones y vistas antes de descargar, y se
descarta lo que no sirve sin gastar ancho de banda.
"""

import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .. import downloader
from ..models import Candidato, RegistroVideo
from .base import BaseScraper, Log, hashtags
from .navegador import Navegador, NavegadorNoDisponible

# Rutas de la API web que traen listas de videos, y la clave donde vienen.
APIS = (
    "/api/search/item/full/",
    "/api/search/general/full/",
    "/api/challenge/item_list/",
)
MAX_SCROLLS = 25
SCROLLS_SIN_NOVEDAD = 3
PAUSA_SCROLL_MS = 2500
MAX_HASHTAGS = 4
SELECTOR_CAPTCHA = ('[id^="captcha"], [class*="captcha_verify"], '
                    '[class*="captcha-verify"], #tiktok-verify-ele')


def _items(datos: dict) -> list:
    if not isinstance(datos, dict):
        return []
    items = datos.get("itemList") or datos.get("item_list")
    if items:
        return items
    # search/general/full mezcla usuarios, videos, etc. en data[].item
    return [d.get("item") for d in (datos.get("data") or [])
            if isinstance(d, dict) and isinstance(d.get("item"), dict)]


def _entero(valor) -> Optional[int]:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def candidato_de_item(item: dict) -> Optional[Candidato]:
    """Convierte un item de la API web de TikTok. None si no es un video."""
    vid = str(item.get("id") or "")
    if not vid.isdigit() or item.get("imagePost"):
        return None                     # los carruseles de fotos no son video
    autor = (item.get("author") or {}).get("uniqueId") or ""
    video = item.get("video") or {}
    stats = item.get("statsV2") or item.get("stats") or {}
    fecha = ""
    if item.get("createTime"):
        try:
            fecha = datetime.fromtimestamp(int(item["createTime"]), timezone.utc).strftime("%Y%m%d")
        except (TypeError, ValueError, OSError):
            pass
    return Candidato(
        video_id=vid,
        plataforma="tiktok",
        titulo=item.get("desc") or "",
        url="https://www.tiktok.com/@%s/video/%s" % (autor or "_", vid),
        duracion=float(video["duration"]) if video.get("duration") else None,
        canal=autor,
        origen="navegador",
        ancho=_entero(video.get("width")),
        alto=_entero(video.get("height")),
        vistas=_entero(stats.get("playCount")),
        fecha_subida=fecha,
    )


class _Coleccion(object):
    """Acumula candidatos sin repetir y sin que un autor cope el corpus."""

    def __init__(self, objetivo: int, excluir: Set[str], settings):
        self.objetivo = objetivo
        self.excluir = excluir
        self.settings = settings
        self.max_por_autor = max(2, objetivo // 4)
        self.vistos: Set[str] = set()
        self.items: List[Candidato] = []
        self.reserva: List[Candidato] = []
        self.por_autor: Dict[str, int] = {}
        self.descartados = 0

    def lleno(self) -> bool:
        return len(self.items) >= self.objetivo

    def agregar(self, cand: Optional[Candidato]) -> bool:
        if cand is None or cand.video_id in self.vistos or cand.video_id in self.excluir:
            return False
        self.vistos.add(cand.video_id)
        if downloader.motivo_descarte(self.settings, cand.duracion, cand.ancho, cand.alto):
            self.descartados += 1
            return False
        if self.por_autor.get(cand.canal, 0) >= self.max_por_autor:
            self.reserva.append(cand)
            return False
        self.por_autor[cand.canal] = self.por_autor.get(cand.canal, 0) + 1
        self.items.append(cand)
        return True

    def cerrar(self) -> List[Candidato]:
        while self.reserva and not self.lleno():
            self.items.append(self.reserva.pop(0))
        return self.items


class TikTokScraper(BaseScraper):
    PLATAFORMA = "tiktok"

    def buscar(self, tema: str, limite: int, ids_excluir: Set[str],
               log: Optional[Log] = None, cancel_event=None) -> List[Candidato]:
        log = log or (lambda _m: None)
        self.backend = "navegador"
        col = _Coleccion(max(limite * 2, limite + 10), set(ids_excluir or ()), self.settings)

        fuentes = [("busqueda '%s'" % tema,
                    "https://www.tiktok.com/search/video?q=%s" % urllib.parse.quote(tema))]
        for tag in hashtags(tema)[:MAX_HASHTAGS]:
            fuentes.append(("#%s" % tag, "https://www.tiktok.com/tag/%s" % urllib.parse.quote(tag)))

        try:
            with Navegador(self.settings, self.PLATAFORMA) as nav:
                self._recorrer(nav, fuentes, col, log, cancel_event)
        except NavegadorNoDisponible as exc:
            log("[TikTok] no se puede buscar: %s" % exc)
            return []

        if col.descartados:
            log("[TikTok] %d resultados descartados antes de bajar (duracion u orientacion)"
                % col.descartados)
        return col.cerrar()

    # -- recorrido de la web ----------------------------------------------
    def _recorrer(self, nav, fuentes, col, log, cancel_event):
        cancelado = lambda: cancel_event is not None and cancel_event.is_set()
        estado = {"nuevos": 0, "vacias": 0}

        def al_responder(resp):
            if not any(api in resp.url for api in APIS):
                return
            try:
                datos = resp.json()
            except Exception:
                # 200 con cuerpo vacio: asi responde TikTok cuando no hay
                # sesion o sospecha de automatizacion.
                estado["vacias"] += 1
                return
            for item in _items(datos):
                if col.agregar(candidato_de_item(item)):
                    estado["nuevos"] += 1

        pagina = nav.pagina()
        pagina.on("response", al_responder)

        # Primera visita a la portada: fija las cookies (msToken, ttwid) que la
        # API exige; sin ellas las busquedas vuelven vacias.
        pagina.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=60000)
        pagina.wait_for_timeout(3000)
        if not nav.con_sesion():
            log("[TikTok] AVISO: el perfil no tiene sesion iniciada; TikTok suele "
                "bloquear la busqueda a anonimos. Ejecuta una vez: "
                "python main.py --login tiktok")

        for nombre, url in fuentes:
            if col.lleno() or cancelado():
                break
            antes = len(col.items)
            estado["vacias"] = 0
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._desplazar(pagina, col, estado, log, cancelado)
                # Respaldo: enlaces presentes en el DOM que no pasaron por la API.
                for cand in self._enlaces_dom(pagina):
                    col.agregar(cand)
            except Exception as exc:
                log("[TikTok] %s: %s" % (nombre, str(exc).splitlines()[0][:100]))
                continue
            log("[TikTok] %s -> %d candidatos (total %d)"
                % (nombre, len(col.items) - antes, len(col.items)))
            if len(col.items) == antes and estado["vacias"]:
                log("[TikTok] %s: TikTok devolvio respuestas vacias "
                    "(sin sesion o bloqueo temporal)" % nombre)

    def _desplazar(self, pagina, col, estado, log, cancelado):
        quietos = 0
        for _ in range(MAX_SCROLLS):
            if col.lleno() or cancelado():
                return
            estado["nuevos"] = 0
            pagina.wait_for_timeout(PAUSA_SCROLL_MS)
            if self._hay_captcha(pagina):
                if not self._esperar_captcha(pagina, log, cancelado):
                    return
            pagina.mouse.wheel(0, 5000)
            quietos = 0 if estado["nuevos"] else quietos + 1
            if quietos >= SCROLLS_SIN_NOVEDAD:
                return

    @staticmethod
    def _hay_captcha(pagina) -> bool:
        try:
            loc = pagina.locator(SELECTOR_CAPTCHA)
            return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 5)))
        except Exception:
            return False

    def _esperar_captcha(self, pagina, log, cancelado) -> bool:
        """True si el reto desaparecio y se puede seguir."""
        if self.settings.navegador_oculto:
            log("[TikTok] TikTok pidio un captcha y el navegador esta oculto; "
                "desactiva RECOPILADOR_NAVEGADOR_OCULTO o inicia sesion con --login tiktok")
            return False
        log("[TikTok] TikTok pidio un captcha: resuelvelo en la ventana del "
            "navegador (se espera %d s)" % self.settings.espera_captcha)
        esperado = 0.0
        while esperado < self.settings.espera_captcha and not cancelado():
            pagina.wait_for_timeout(2000)
            esperado += 2.0
            if not self._hay_captcha(pagina):
                log("[TikTok] captcha resuelto; se continua")
                pagina.wait_for_timeout(2000)
                return True
        return False

    @staticmethod
    def _enlaces_dom(pagina) -> List[Candidato]:
        try:
            hrefs = pagina.eval_on_selector_all(
                'a[href*="/video/"]', "els => els.map(e => e.href)")
        except Exception:
            return []
        cands = []
        for href in hrefs:
            partes = urllib.parse.urlparse(href).path.strip("/").split("/")
            if len(partes) == 3 and partes[0].startswith("@") and partes[1] == "video" \
                    and partes[2].isdigit():
                cands.append(Candidato(
                    video_id=partes[2], plataforma="tiktok", canal=partes[0][1:],
                    url="https://www.tiktok.com/%s/video/%s" % (partes[0], partes[2]),
                    origen="navegador"))
        return cands

    # -- descarga ---------------------------------------------------------
    def descargar(self, candidato: Candidato, tema: str, cancel_event=None,
                  intento: int = 0) -> RegistroVideo:
        return downloader.descargar(candidato, tema, self.settings,
                                    cancel_event=cancel_event, intento=intento)
