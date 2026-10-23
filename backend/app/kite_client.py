"""Cliente de solo lectura para la plataforma Kite (Movistar IoT) — consumo de datos por SIM.

Kite no tiene API pública para esto (solo portal web), así que se
automatiza con Playwright reusando una sesión ya autenticada — ver
`kite_setup_session.py` para el login inicial (con código de
verificación por mail, hecho a mano una sola vez).

Formato de número es-AR: coma es separador decimal ("4,138 GB" = 4.138
GB), punto sería separador de miles si apareciera.

Selectores confirmados a mano contra la plataforma real (agosto 2026):
- Buscador: input[placeholder*="ICC"]
- Ícono de detalle de la fila: .icon-detail
- Dentro de la ficha, sección "Control de consumo de tráfico", columna
  Datos, toggle "Mes": texto "Consumo de tráfico actual" y
  "Límite (con corte)".

Si Kite corta la sesión (vuelve a pedir usuario/contraseña o el código
de verificación), se levanta KiteSessionExpiredError — hay que correr
`kite_setup_session.py` de nuevo a mano.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.config import KiteConfig

logger = logging.getLogger(__name__)

SESSION_PATH = Path(__file__).resolve().parents[1] / "kite_session.json"

_NUMBER_UNIT_RE = re.compile(r"([\d.,]+)\s*(GB|MB|KB)", re.IGNORECASE)
_UNIT_TO_MB = {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}


class KiteSessionExpiredError(RuntimeError):
    """La sesión guardada ya no sirve — hace falta correr kite_setup_session.py de nuevo."""


class KiteScrapeError(RuntimeError):
    """Algo cambió en la página de Kite y no se pudo leer el consumo (selector roto, etc.)."""


@dataclass(frozen=True)
class SimUsage:
    icc: str
    consumo_mb: float
    limite_mb: float | None  # None si el plan no tiene límite de corte configurado


def _parse_value_to_mb(text: str) -> float:
    """'4,138 GB' -> 4138.0 (formato es-AR: coma decimal, punto de miles)."""
    match = _NUMBER_UNIT_RE.search(text)
    if not match:
        raise KiteScrapeError(f"No se pudo interpretar el valor de consumo: {text!r}")
    number_str, unit = match.groups()
    number_str = number_str.replace(".", "").replace(",", ".")
    try:
        value = float(number_str)
    except ValueError as exc:
        raise KiteScrapeError(f"No se pudo interpretar el número: {text!r}") from exc
    return value * _UNIT_TO_MB[unit.upper()]


def _is_session_expired(url: str) -> bool:
    return "twofactorlogin" in url or url.rstrip("/").endswith("/login") or "/account/login" in url


def fetch_sim_usage(cfg: KiteConfig, iccs: list[str]) -> dict[str, SimUsage]:
    """Consulta el consumo mensual de cada ICC. Se detiene en el primer error de sesión."""
    if not SESSION_PATH.is_file():
        raise KiteSessionExpiredError(
            f"No existe {SESSION_PATH} — correr kite_setup_session.py para loguearse una vez."
        )

    results: dict[str, SimUsage] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, storage_state=str(SESSION_PATH))
        page = context.new_page()

        try:
            page.goto(cfg.login_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            if _is_session_expired(page.url):
                raise KiteSessionExpiredError("La sesión de Kite venció — correr kite_setup_session.py de nuevo.")

            close_btn = page.locator('[data-m2mid="announcement-close-btn"]:visible').first
            if close_btn.count() > 0:
                try:
                    close_btn.click(timeout=3000)
                    page.wait_for_timeout(500)
                except Exception:
                    pass

            for icc in iccs:
                try:
                    usage = _fetch_one(page, icc)
                    if usage is not None:
                        results[icc] = usage
                except KiteSessionExpiredError:
                    raise
                except Exception as exc:
                    logger.warning("No se pudo leer el consumo de la SIM %s: %s", icc, exc)
        finally:
            browser.close()

    return results


def _fetch_one(page, icc: str) -> SimUsage | None:
    search_box = page.locator('input[placeholder*="ICC"]').first
    search_box.click()
    search_box.fill("")
    search_box.fill(icc)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    if _is_session_expired(page.url):
        raise KiteSessionExpiredError("La sesión de Kite venció — correr kite_setup_session.py de nuevo.")

    detail_icon = page.locator(".icon-detail").first
    if detail_icon.count() == 0:
        logger.warning("SIM %s no encontrada en Kite.", icc)
        return None

    detail_icon.click(timeout=5000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    section = page.locator("text=Control de consumo de tráfico").first
    if section.count() == 0:
        raise KiteScrapeError("No se encontró la sección 'Control de consumo de tráfico' en la ficha de la SIM.")
    section.click(timeout=5000)
    page.wait_for_timeout(800)

    # Panel de "Datos" identificado de forma estable (no por posición): data-m2mid="lampData-panel".
    datos_panel = page.locator('[data-m2mid="lampData-panel"]').first
    if datos_panel.count() == 0:
        raise KiteScrapeError("No se encontró el panel de Datos (lampData-panel) en la ficha de la SIM.")

    # Asegurar que el toggle esté en "Mes" (no "Hoy") sin tocarlo si ya está activo,
    # porque Kite pre-renderiza ambos estados en el DOM y solo alterna la visibilidad.
    monthly_btn = datos_panel.locator(".monthly").first
    monthly_classes = monthly_btn.get_attribute("class") or ""
    if "x-pressed" not in monthly_classes:
        monthly_btn.click(timeout=3000)
        page.wait_for_timeout(500)

    consumed_el = datos_panel.locator('[data-m2mid="consumed"]:visible').first
    limit_el = datos_panel.locator('[data-m2mid="limit"]:visible').first
    if consumed_el.count() == 0 or limit_el.count() == 0:
        raise KiteScrapeError("No se encontraron los valores de consumo/límite en el panel de Datos.")

    consumo_value = consumed_el.get_attribute("data-m2mvalue")
    limite_value = limit_el.get_attribute("data-m2mvalue")
    if not consumo_value:
        raise KiteScrapeError("El atributo data-m2mvalue de 'consumed' vino vacío.")

    consumo_mb = _parse_value_to_mb(consumo_value)
    limite_mb = None
    if limite_value and "N/A" not in limite_value:
        try:
            limite_mb = _parse_value_to_mb(limite_value)
        except KiteScrapeError:
            limite_mb = None

    return SimUsage(icc=icc, consumo_mb=consumo_mb, limite_mb=limite_mb)
