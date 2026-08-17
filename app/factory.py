"""Flask application factory. One deployable; service modules inside (STRATEGY §2-3)."""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify

from app.platform import redis_clients as rc
from app.platform.config import load_config
from app.platform.db import init_engine
from app.platform.errors import register_error_handlers


def create_app() -> Flask:
    cfg = load_config()

    app = Flask(__name__)
    app.config["APP_CONFIG"] = cfg
    app.config["SECRET_KEY"] = cfg.secret_key

    logging.basicConfig(
        level=logging.INFO if cfg.is_production else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    init_engine(cfg.database_url, echo=False)
    # Fallback is development-only; production must have a real Redis (STRATEGY §6).
    rc.init_redis(
        cfg.redis_durable_url, cfg.redis_cache_url,
        allow_fallback=not cfg.is_production,
    )

    register_error_handlers(app)

    from app.auth.routes import account_bp, bp as auth_bp
    from app.profile.routes import bp as core_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(core_bp)

    if not cfg.is_production:
        # Never mounted in production - it exists to drive the API by hand.
        from app.devconsole.routes import bp as dev_bp
        app.register_blueprint(dev_bp)

    @app.get("/")
    def index():
        """Dev-friendly root. A JSON API whose root 404s is hostile to anyone
        poking at it in a browser; this lists what actually exists."""
        routes = []
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
            if rule.endpoint == "static":
                continue
            routes.append({
                "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
                "path": rule.rule,
            })
        return jsonify({
            "service": "fitness-app-api",
            "env": cfg.env,
            "note": "JSON API only - there is no web client (Module 11).",
            "routes": routes,
        })

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "env": cfg.env})

    @app.get("/v1/app/config")
    def app_config():
        """11.10 - unauthenticated, checked on launch before login."""
        return jsonify({
            "minimum_supported_version": os.environ.get("MIN_APP_VERSION", "1.0.0"),
            "soft_update_version": os.environ.get("SOFT_UPDATE_VERSION", "1.0.0"),
        })

    return app
