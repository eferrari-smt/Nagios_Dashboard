"""Login interactivo único a Kite — vos ponés el código de verificación a mano.

Abre un Chrome VISIBLE (no headless) con usuario/contraseña ya cargados.
Cuando te llegue el mail con el código de verificación, escribilo en esa
ventana y hacé click en "Iniciar sesión" vos mismo. El script espera a
que la navegación cambie de la pantalla de login/2FA y ahí guarda la
sesión (cookies) en kite_session.json — así los próximos scripts pueden
reusarla sin pedir el código de nuevo (si Kite lo permite).

Uso:
    python kite_setup_session.py
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
        print("Falta KITE_LOGIN_URL / KITE_USERNAME / KITE_PASSWORD en el .env.")
        return 1

    OUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.goto(cfg.login_url, wait_until="networkidle", timeout=30000)

        reject_btn = page.locator("#onetrust-reject-all-handler")
        if reject_btn.count() > 0:
            try:
                reject_btn.click(timeout=5000)
            except Exception:
                pass

        page.fill("#identity", cfg.username)
        page.fill("#password", cfg.password)
        page.click('[kiteid="login-button"]')

        print("\n>>> Se abrió Chrome. Cuando te llegue el código por mail, escribilo ahí")
        print(">>> y hacé click en 'Iniciar sesión'. Esperando (hasta 5 minutos)...\n")

        try:
            # Esperamos a que la URL deje de tener /twofactorlogin — señal de login completo.
            page.wait_for_url(lambda url: "twofactorlogin" not in url, timeout=300000)
        except Exception as exc:
            print(f"No se detectó el login completo a tiempo: {exc}")
            page.screenshot(path=str(OUT_DIR / "03_setup_timeout.png"), full_page=True)
            browser.close()
            return 1

        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        context.storage_state(path=str(SESSION_PATH))
        page.screenshot(path=str(OUT_DIR / "03_setup_success.png"), full_page=True)

        print(f"Sesión guardada en: {SESSION_PATH}")
        print(f"URL final: {page.url}")

        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
