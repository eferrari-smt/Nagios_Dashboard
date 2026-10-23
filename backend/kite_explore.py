"""Reconocimiento de la plataforma Kite (Movistar) — Etapa 8: submenú de columnas mensuales.

Uso:
    python kite_explore.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.config import load_kite_config

OUT_DIR = Path(__file__).resolve().parent / "kite_explore_output"
SESSION_PATH = Path(__file__).resolve().parent / "kite_session.json"


def main() -> int:
    cfg = load_kite_config()
    if cfg is None:
        print("Falta configuración de Kite en el .env.")
        return 1

    OUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, storage_state=str(SESSION_PATH))
        page = context.new_page()

        page.goto(cfg.login_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        close_btn = page.locator('[data-m2mid="announcement-close-btn"]:visible').first
        if close_btn.count() > 0:
            try:
                close_btn.click(timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                pass

        search_box = page.locator('input[placeholder*="ICC"]').first
        search_box.click()
        search_box.fill("8954000000000000001")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        page.click("text=Columnas", timeout=5000)
        page.wait_for_timeout(800)
        page.hover("text=Consumo de tráfico mensual", timeout=5000)
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT_DIR / "11_columnas_mensual_submenu.png"), full_page=True)

        browser.close()

    print(f"Listo. Revisá los archivos en: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
