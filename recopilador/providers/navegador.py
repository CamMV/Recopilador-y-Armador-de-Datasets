# -*- coding: utf-8 -*-
"""Navegador automatizado con perfil persistente (Playwright).

TikTok ya no deja buscar ni listar hashtags a visitantes anonimos: responde
vacio o muestra un captcha de deslizador. En vez de intentar esquivarlo, se usa
un perfil de Chromium propio del proyecto en el que la persona inicia sesion una
vez (`python main.py --login tiktok`); las corridas siguientes reutilizan esa
sesion. Si aun asi aparece un reto, la ventana esta a la vista y se espera a que
la persona lo resuelva.

El mismo perfil sirve para Instagram: tras iniciar sesion se exportan sus
cookies en formato Netscape para yt-dlp, y la cookie `sessionid` para instagrapi.
"""

import io
import time
from pathlib import Path
from typing import Callable, Optional

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

URL_LOGIN = {
    "tiktok": "https://www.tiktok.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
}
DOMINIO = {
    "tiktok": "tiktok.com",
    "instagram": "instagram.com",
}
# Cookie cuya presencia indica que hay una sesion iniciada.
COOKIE_SESION = {
    "tiktok": "sessionid",
    "instagram": "sessionid",
}


class NavegadorNoDisponible(Exception):
    """Falta Playwright o su Chromium."""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise NavegadorNoDisponible(
            "falta Playwright. Instalalo con:\n"
            "    pip install playwright\n"
            "    python -m playwright install chromium")
    return sync_playwright


class Navegador(object):
    """Contexto persistente de Chromium. Usar con `with`."""

    def __init__(self, settings, plataforma: str, oculto: Optional[bool] = None):
        self.settings = settings
        self.plataforma = plataforma
        self.oculto = settings.navegador_oculto if oculto is None else oculto
        self._pw = None
        self.contexto = None

    def __enter__(self):
        perfil = self.settings.perfil_navegador(self.plataforma)
        perfil.mkdir(parents=True, exist_ok=True)
        self._pw = _playwright()().start()
        opciones = dict(
            user_data_dir=str(perfil),
            headless=self.oculto,
            locale="es-ES",
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            # El Chromium completo en modo oculto se parece mas a un navegador
            # normal que el "headless shell" que Playwright usa por defecto.
            self.contexto = self._pw.chromium.launch_persistent_context(
                channel="chromium", **opciones)
        except Exception as exc:
            self._pw.stop()
            raise NavegadorNoDisponible(
                "no se pudo abrir Chromium (%s). Instalalo con:\n"
                "    python -m playwright install chromium" % str(exc).splitlines()[0])
        return self

    def __exit__(self, *_exc):
        self.cerrar()

    def cerrar(self):
        try:
            if self.contexto is not None:
                self.contexto.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self.contexto = self._pw = None

    def pagina(self):
        paginas = self.contexto.pages
        return paginas[0] if paginas else self.contexto.new_page()

    def con_sesion(self) -> bool:
        nombre = COOKIE_SESION[self.plataforma]
        return any(c["name"] == nombre and c.get("value")
                   for c in self.cookies())

    def cookies(self) -> list:
        dominio = DOMINIO[self.plataforma]
        return [c for c in self.contexto.cookies()
                if dominio in (c.get("domain") or "")]

    def exportar_cookies(self) -> Optional[Path]:
        """Escribe <sesiones>/<plataforma>_cookies.txt para yt-dlp."""
        cookies = self.cookies()
        if not cookies:
            return None
        return escribir_cookies_netscape(
            cookies, self.settings.sesiones_dir / ("%s_cookies.txt" % self.plataforma))


def escribir_cookies_netscape(cookies: list, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with io.open(str(ruta), "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            dominio = c.get("domain") or ""
            expira = c.get("expires") or 0
            f.write("\t".join([
                dominio,
                "TRUE" if dominio.startswith(".") else "FALSE",
                c.get("path") or "/",
                "TRUE" if c.get("secure") else "FALSE",
                str(int(expira)) if expira and expira > 0 else "0",
                c["name"],
                c.get("value") or "",
            ]) + "\n")
    return ruta


def iniciar_sesion(settings, plataforma: str,
                   log: Optional[Callable[[str], None]] = None,
                   espera_max: float = 600.0) -> bool:
    """Abre una ventana para que la persona inicie sesion.

    Termina cuando se detecta la sesion (y unos segundos mas, para que la
    pagina termine de fijar cookies), cuando se cierra la ventana o al agotar
    `espera_max`. Devuelve True si quedo una sesion guardada.
    """
    log = log or print
    if plataforma not in URL_LOGIN:
        raise ValueError("no hay inicio de sesion para '%s'" % plataforma)

    with Navegador(settings, plataforma, oculto=False) as nav:
        pagina = nav.pagina()
        pagina.goto(URL_LOGIN[plataforma], wait_until="domcontentloaded", timeout=60000)
        if nav.con_sesion():
            log("[%s] ya habia una sesion iniciada en el perfil." % plataforma)
        else:
            log("[%s] inicia sesion en la ventana que se acaba de abrir "
                "(tienes %d minutos)." % (plataforma, espera_max // 60))
            fin = time.time() + espera_max
            while time.time() < fin and not nav.con_sesion():
                if pagina.is_closed():
                    break
                try:
                    pagina.wait_for_timeout(1500)
                except Exception:
                    break               # ventana cerrada por la persona
            if nav.con_sesion():
                try:
                    pagina.wait_for_timeout(4000)
                except Exception:
                    pass

        ok = nav.con_sesion()
        if ok:
            ruta = nav.exportar_cookies()
            log("[%s] sesion guardada en %s; cookies para yt-dlp en %s"
                % (plataforma, settings.perfil_navegador(plataforma), ruta))
            if plataforma == "instagram":
                # Se descarta la sesion previa de instagrapi: la nueva manda.
                from .instagram import ARCHIVO_SESION
                viejo = settings.sesiones_dir / ARCHIVO_SESION
                if viejo.exists():
                    viejo.unlink()
        else:
            log("[%s] no se detecto ninguna sesion iniciada." % plataforma)
        return ok


def sessionid_guardado(settings, plataforma: str) -> Optional[str]:
    """Valor de la cookie `sessionid` exportada por iniciar_sesion()."""
    ruta = settings.sesiones_dir / ("%s_cookies.txt" % plataforma)
    if not ruta.exists():
        return None
    with io.open(str(ruta), encoding="utf-8") as f:
        for linea in f:
            partes = linea.rstrip("\n").split("\t")
            if len(partes) == 7 and partes[5] == "sessionid" and partes[6]:
                return partes[6]
    return None
