"""
Main Entry Point

Run this file to start the Bittensor Tracker API server.

Usage:
    python run.py                    # Start in development mode
    gunicorn run:app -b 0.0.0.0:5000 # Start in production mode
"""

import logging
import sys
import atexit

from src.config import config
from src.api.app import create_app

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Create Flask app
app = create_app()

# Background scheduler for pre-fetching subnet data
scheduler = None


def start_background_refresh():
    """Start APScheduler to periodically refresh subnet cache."""
    global scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()

        def refresh_subnets():
            """Background job to refresh subnet cache."""
            try:
                from src.services.bittensor_service import get_bittensor_service
                service = get_bittensor_service(
                    network=config.BITTENSOR_NETWORK,
                    cache_ttl=config.SUBNET_CACHE_TTL
                )
                subnets = service.get_all_subnets(use_cache=False)
                logger.info(f"Background refresh: fetched {len(subnets)} subnets")
            except Exception as e:
                logger.error(f"Background refresh failed: {e}")

        # Refresh subnets every REFRESH_INTERVAL seconds
        scheduler.add_job(refresh_subnets, 'interval', seconds=config.REFRESH_INTERVAL,
                          id='refresh_subnets', replace_existing=True)

        scheduler.start()
        logger.info(f"Background refresh started (subnets every {config.REFRESH_INTERVAL}s)")

        # Shut down scheduler on exit
        atexit.register(lambda: scheduler.shutdown(wait=False))

        # Run subnet fetch in background thread
        import threading
        threading.Thread(target=refresh_subnets, daemon=True).start()
        logger.info("Initial subnet fetch started in background thread")

    except ImportError:
        logger.warning("apscheduler not installed — background refresh disabled. "
                        "Install with: pip install apscheduler")
    except Exception as e:
        logger.error(f"Failed to start background scheduler: {e}")


def main():
    """Run the development server."""
    logger.info(f"Starting Bittensor Tracker API on {config.HOST}:{config.PORT}")
    logger.info(f"Network: {config.BITTENSOR_NETWORK}")
    logger.info(f"Debug mode: {config.DEBUG}")

    print("\n" + "=" * 60)
    print("Bittensor Subnet Tracker API")
    print("=" * 60)
    print(f"\nServer running at: http://{config.HOST}:{config.PORT}")
    print(f"Network: {config.BITTENSOR_NETWORK}")
    print("\nEndpoints:")
    print(f"  - Health:          http://localhost:{config.PORT}/api/v1/health")
    print(f"  - Subnets:         http://localhost:{config.PORT}/api/v1/subnets")
    print(f"  - Sheets Subnets:  http://localhost:{config.PORT}/api/v1/sheets/subnets")
    print(f"  - Sheets Portfolio:http://localhost:{config.PORT}/api/v1/sheets/portfolio?address=<SS58>")
    print(f"  - Sheets Stakes:   http://localhost:{config.PORT}/api/v1/sheets/stakes?address=<SS58>")
    print(f"  - Wallet Portfolio:http://localhost:{config.PORT}/api/v1/wallet/<address>/portfolio")
    print(f"  - Wallet Stakes:   http://localhost:{config.PORT}/api/v1/wallet/<address>/stakes")
    print("\nGoogle Sheets Usage:")
    print(f'  =IMPORTDATA("http://your-server:{config.PORT}/api/v1/sheets/subnets")')
    print(f'  =IMPORTDATA("http://your-server:{config.PORT}/api/v1/sheets/portfolio?address=5Cai...")')
    print(f'  =IMPORTDATA("http://your-server:{config.PORT}/api/v1/sheets/stakes?address=5Cai...")')
    print("=" * 60 + "\n")

    # Start background refresh
    start_background_refresh()

    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG
    )


if __name__ == '__main__':
    main()
