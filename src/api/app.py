"""
Flask Application Factory

Creates and configures the Flask application.
"""

import logging
import os
import atexit
import threading
from flask import Flask
from flask_cors import CORS

from .routes import api

logger = logging.getLogger(__name__)

_scheduler_started = False


def create_app(config: dict = None) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        config: Optional configuration dictionary

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    # Default configuration
    app.config.update({
        'JSON_SORT_KEYS': False,
        'JSONIFY_PRETTYPRINT_REGULAR': True,
    })

    # Override with provided config
    if config:
        app.config.update(config)

    # Enable CORS for Google Sheets access
    CORS(app, origins="*", allow_headers=["Content-Type"])

    # Register blueprints
    app.register_blueprint(api)

    # Root endpoint
    @app.route('/')
    def index():
        return {
            'service': 'Bittensor Subnet Tracker API',
            'version': '1.0.0',
            'endpoints': {
                'health': '/api/v1/health',
                'all_subnets': '/api/v1/subnets',
                'subnet_by_id': '/api/v1/subnets/<netuid>',
                'subnet_emissions': '/api/v1/subnets/emissions',
                'wallet_portfolio': '/api/v1/wallet/<address>/portfolio',
                'wallet_stakes': '/api/v1/wallet/<address>/stakes',
                'sheets_subnets': '/api/v1/sheets/subnets',
                'sheets_portfolio': '/api/v1/sheets/portfolio?address=<SS58>',
                'sheets_stakes': '/api/v1/sheets/stakes?address=<SS58>',
                'current_block': '/api/v1/block'
            },
            'usage': {
                'google_sheets_subnets': '=IMPORTDATA("https://your-api-url/api/v1/sheets/subnets")',
                'google_sheets_portfolio': '=IMPORTDATA("https://your-api-url/api/v1/sheets/portfolio?address=5Cai...")',
                'google_sheets_stakes': '=IMPORTDATA("https://your-api-url/api/v1/sheets/stakes?address=5Cai...")',
                'json_format': 'Add ?format=json to any endpoint',
                'csv_format': 'Add ?format=csv to any endpoint'
            }
        }

    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return {'error': 'Endpoint not found'}, 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {error}")
        return {'error': 'Internal server error'}, 500

    # Start background refresh (works under both gunicorn and dev server)
    _start_background_refresh()

    logger.info("Flask application created successfully")
    return app


def _start_background_refresh():
    """Start background scheduler for periodic data refresh."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from ..config import config
        from ..services.bittensor_service import get_bittensor_service

        scheduler = BackgroundScheduler()

        def refresh_subnets():
            try:
                service = get_bittensor_service(
                    network=config.BITTENSOR_NETWORK,
                    cache_ttl=config.SUBNET_CACHE_TTL
                )
                subnets = service.get_all_subnets(use_cache=False)
                logger.info(f"Background refresh: fetched {len(subnets)} subnets")
            except Exception as e:
                logger.error(f"Background refresh failed: {e}")

        scheduler.add_job(refresh_subnets, 'interval',
                          seconds=config.REFRESH_INTERVAL,
                          id='refresh_subnets', replace_existing=True)

        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False))

        logger.info(f"Background refresh started (subnets every {config.REFRESH_INTERVAL}s)")

        # Fetch subnets in background thread
        threading.Thread(target=refresh_subnets, daemon=True).start()

    except ImportError:
        logger.warning("apscheduler not installed - background refresh disabled")
    except Exception as e:
        logger.error(f"Failed to start background scheduler: {e}")
