"""
Configuration Module

Loads configuration from environment variables.
"""

import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()


class Config:
    """Application configuration."""

    # Flask
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('FLASK_DEBUG', '0') == '1'
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5000))

    # Bittensor
    BITTENSOR_NETWORK = os.getenv('BITTENSOR_NETWORK', 'finney')

    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

    # Cache TTL (in seconds)
    SUBNET_CACHE_TTL = int(os.getenv('SUBNET_CACHE_TTL', 300))   # 5 minutes
    PRICE_CACHE_TTL = int(os.getenv('PRICE_CACHE_TTL', 30))      # 30 seconds
    WALLET_CACHE_TTL = int(os.getenv('WALLET_CACHE_TTL', 120))   # 2 minutes

    # Taostats API
    TAOSTATS_API_KEY = os.getenv('TAOSTATS_API_KEY', '')

    # Background refresh interval (seconds)
    REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 300))    # 5 minutes


config = Config()
