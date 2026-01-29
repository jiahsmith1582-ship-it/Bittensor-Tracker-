"""
Price Service

Fetches TAO price from external APIs.
"""

import logging
import requests
from typing import Optional
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class TaoPrice:
    """Data class for TAO price information."""
    price_usd: float
    price_aud: Optional[float]
    price_btc: Optional[float]
    market_cap_usd: Optional[float]
    volume_24h_usd: Optional[float]
    change_24h_percent: Optional[float]
    source: str
    timestamp: str


class PriceService:
    """Service for fetching TAO price from various sources."""

    COINGECKO_API = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self._cached_price: Optional[TaoPrice] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 30  # Cache for 30 seconds

    def get_tao_price(self, use_cache: bool = True) -> Optional[TaoPrice]:
        """
        Fetch current TAO price.

        Args:
            use_cache: Whether to use cached data if available

        Returns:
            TaoPrice object or None if failed
        """
        # Check cache
        if use_cache and self._cached_price and self._cache_timestamp:
            cache_age = (datetime.now() - self._cache_timestamp).total_seconds()
            if cache_age < self._cache_ttl_seconds:
                logger.debug("Returning cached TAO price")
                return self._cached_price

        # Try CoinGecko first
        price = self._fetch_from_coingecko()

        if price:
            self._cached_price = price
            self._cache_timestamp = datetime.now()
            return price

        # Fallback: try alternative sources
        price = self._fetch_from_alternative()

        if price:
            self._cached_price = price
            self._cache_timestamp = datetime.now()

        return price

    def _fetch_from_coingecko(self) -> Optional[TaoPrice]:
        """Fetch TAO price from CoinGecko API."""
        try:
            url = f"{self.COINGECKO_API}/simple/price"
            params = {
                "ids": "bittensor",
                "vs_currencies": "usd,aud,btc",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true"
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "bittensor" not in data:
                logger.warning("Bittensor not found in CoinGecko response")
                return None

            tao_data = data["bittensor"]

            return TaoPrice(
                price_usd=tao_data.get("usd", 0),
                price_aud=tao_data.get("aud"),
                price_btc=tao_data.get("btc"),
                market_cap_usd=tao_data.get("usd_market_cap"),
                volume_24h_usd=tao_data.get("usd_24h_vol"),
                change_24h_percent=tao_data.get("usd_24h_change"),
                source="coingecko",
                timestamp=datetime.now().isoformat()
            )

        except requests.RequestException as e:
            logger.error(f"CoinGecko API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing CoinGecko response: {e}")
            return None

    def _fetch_from_alternative(self) -> Optional[TaoPrice]:
        """Fetch TAO price from alternative sources."""
        # Try CoinMarketCap (requires API key, but has free tier)
        # For now, return None as fallback
        logger.warning("No alternative price source available")
        return None

    def to_dict(self, price: TaoPrice) -> dict:
        """Convert TaoPrice to dict for JSON serialization."""
        return asdict(price)


# Singleton instance
_service: Optional[PriceService] = None


def get_price_service() -> PriceService:
    """Get or create the Price service singleton."""
    global _service
    if _service is None:
        _service = PriceService()
    return _service
