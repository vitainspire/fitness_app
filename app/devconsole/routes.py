"""Development-only UI harness.

Two things live here, neither ships:
  /dev      raw API console (request/response log)
  /ui       the design mockups, plus a working auth screen in the same design

Registered only when FLASK_ENV != production. The product UI is a mobile app
(Module 11); the backend serves JSON exclusively.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, render_template, send_from_directory

bp = Blueprint("devconsole", __name__,
               template_folder="templates", static_folder="static")

MOCKUPS = Path(__file__).parent / "static" / "mockups"

PAGES = {
    "1": ("page1.html", "Onboarding"),
    "2": ("page2.html", "Activity Logging"),
    "3": ("page3.html", "Rehab & Pain Chat"),
    "4": ("page4.html", "Video Library"),
    "5": ("page5.html", "Settings"),
}


@bp.get("/app")
def app_ui():
    """The application itself, wired to the real API."""
    return render_template("app.html")


@bp.get("/dev")
def console():
    return render_template("console.html")


@bp.get("/ui")
def ui_index():
    available = {k: (f, t, (MOCKUPS / f).exists()) for k, (f, t) in PAGES.items()}
    return render_template("ui_index.html", pages=available)


@bp.get("/ui/auth")
def ui_auth():
    """Signup / login in the mockups' design language.

    Deliberately not one of the five: the mockups have no auth screen at all,
    so there is nothing to reproduce here — this fills the gap.
    """
    return render_template("ui_auth.html")


@bp.get("/ui/<page>")
def ui_page(page: str):
    if page not in PAGES:
        abort(404)
    filename = PAGES[page][0]
    if not (MOCKUPS / filename).exists():
        abort(404)
    return send_from_directory(MOCKUPS, filename)
