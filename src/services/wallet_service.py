"""
Wallet Portfolio Service

Queries the Bittensor blockchain for wallet portfolio data including
TAO balance, per-subnet alpha stakes, and portfolio valuation.
Uses substrate-interface directly (Windows compatible).
"""

import logging
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime

from .bittensor_service import get_bittensor_service, _rao_to_tao
from .price_service import get_price_service

logger = logging.getLogger(__name__)


@dataclass
class SubnetStake:
    """Stake information for a single subnet."""
    netuid: int
    subnet_name: str
    symbol: str
    hotkey: str
    tao_staked: float       # TAO staked in this subnet
    alpha_held: float       # Alpha tokens held
    alpha_price: float      # Current alpha price in TAO
    alpha_value_tao: float  # Alpha value in TAO (alpha_held * alpha_price)
    alpha_value_usd: float  # Alpha value in USD


@dataclass
class WalletPortfolio:
    """Complete wallet portfolio."""
    coldkey: str
    free_balance_tao: float
    free_balance_usd: float
    total_staked_tao: float
    total_alpha_value_tao: float
    total_portfolio_tao: float
    total_portfolio_usd: float
    tao_price_usd: float
    subnet_stakes: list
    timestamp: str


class WalletService:
    """Service for querying wallet portfolio data from the Bittensor blockchain."""

    def __init__(self, cache_ttl: int = 120):
        self._cache: dict[str, WalletPortfolio] = {}
        self._cache_timestamps: dict[str, datetime] = {}
        self._cache_ttl_seconds = cache_ttl

    def get_portfolio(self, coldkey_ss58: str, use_cache: bool = True) -> Optional[WalletPortfolio]:
        """
        Get full portfolio for a wallet address.

        Args:
            coldkey_ss58: The SS58 wallet address (coldkey)
            use_cache: Whether to use cached data

        Returns:
            WalletPortfolio or None on failure
        """
        # Check cache
        if use_cache and coldkey_ss58 in self._cache:
            cache_ts = self._cache_timestamps.get(coldkey_ss58)
            if cache_ts:
                age = (datetime.now() - cache_ts).total_seconds()
                if age < self._cache_ttl_seconds:
                    return self._cache[coldkey_ss58]

        try:
            bt_service = get_bittensor_service()
            if not bt_service._ensure_connected():
                return None

            substrate = bt_service.substrate

            # Get TAO price for USD conversions
            price_service = get_price_service()
            tao_price = price_service.get_tao_price()
            tao_usd = tao_price.price_usd if tao_price else 0.0

            # Get free balance
            free_balance = self._get_balance(substrate, coldkey_ss58)

            # Get all subnet stakes for this coldkey
            subnet_stakes = self._get_all_stakes(substrate, coldkey_ss58, tao_usd, bt_service)

            # Calculate totals
            total_staked_tao = sum(s.tao_staked for s in subnet_stakes)
            total_alpha_value_tao = sum(s.alpha_value_tao for s in subnet_stakes)
            total_portfolio_tao = free_balance + total_staked_tao + total_alpha_value_tao
            total_portfolio_usd = total_portfolio_tao * tao_usd

            portfolio = WalletPortfolio(
                coldkey=coldkey_ss58,
                free_balance_tao=round(free_balance, 6),
                free_balance_usd=round(free_balance * tao_usd, 2),
                total_staked_tao=round(total_staked_tao, 6),
                total_alpha_value_tao=round(total_alpha_value_tao, 6),
                total_portfolio_tao=round(total_portfolio_tao, 6),
                total_portfolio_usd=round(total_portfolio_usd, 2),
                tao_price_usd=round(tao_usd, 2),
                subnet_stakes=[asdict(s) for s in subnet_stakes],
                timestamp=datetime.now().isoformat()
            )

            self._cache[coldkey_ss58] = portfolio
            self._cache_timestamps[coldkey_ss58] = datetime.now()
            return portfolio

        except Exception as e:
            logger.error(f"Failed to get portfolio for {coldkey_ss58[:8]}...: {e}")
            return None

    def _get_balance(self, substrate, coldkey_ss58: str) -> float:
        """Get free TAO balance for a coldkey."""
        try:
            result = substrate.query(
                module="System",
                storage_function="Account",
                params=[coldkey_ss58]
            )
            if result and hasattr(result, 'value'):
                data = result.value.get('data', {})
                free = data.get('free', 0)
                return _rao_to_tao(free)
            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get balance: {e}")
            return 0.0

    def _get_all_stakes(self, substrate, coldkey_ss58: str, tao_usd: float,
                         bt_service) -> list[SubnetStake]:
        """Get all stake positions across all subnets for a coldkey."""
        stakes = []

        try:
            # Step 1: Get all hotkeys associated with this coldkey
            hotkeys = []
            try:
                result = substrate.query(
                    module="SubtensorModule",
                    storage_function="StakingHotkeys",
                    params=[coldkey_ss58]
                )
                if result and hasattr(result, 'value') and result.value:
                    hotkeys = [str(h) for h in result.value]
            except Exception as e:
                logger.debug(f"StakingHotkeys query failed: {e}")

            if not hotkeys:
                # Fallback: try OwnedHotkeys
                try:
                    result = substrate.query(
                        module="SubtensorModule",
                        storage_function="OwnedHotkeys",
                        params=[coldkey_ss58]
                    )
                    if result and hasattr(result, 'value') and result.value:
                        hotkeys = [str(h) for h in result.value]
                except Exception as e:
                    logger.debug(f"OwnedHotkeys query failed: {e}")

            if not hotkeys:
                logger.info(f"No hotkeys found for {coldkey_ss58[:8]}...")
                return stakes

            # Ensure subnet data is loaded
            all_subnets = bt_service.get_all_subnets()
            netuid_list = [s.netuid for s in all_subnets]

            # Step 2: For each hotkey, query Alpha stake per subnet
            for hotkey in hotkeys:
                for netuid in netuid_list:
                    try:
                        alpha_result = substrate.query(
                            module="SubtensorModule",
                            storage_function="Alpha",
                            params=[hotkey, netuid, coldkey_ss58]
                        )
                        alpha_val = float(alpha_result.value) if alpha_result and alpha_result.value else 0

                        if alpha_val > 0:
                            alpha_val = _rao_to_tao(alpha_val)

                            subnet_info = bt_service.get_subnet_by_netuid(netuid)
                            alpha_price = subnet_info.alpha_price if subnet_info else 0.0
                            subnet_name = subnet_info.name if subnet_info else f'Subnet {netuid}'
                            symbol = subnet_info.symbol if subnet_info else f'SN{netuid}'

                            alpha_value_tao = alpha_val * alpha_price
                            alpha_value_usd = alpha_value_tao * tao_usd

                            stakes.append(SubnetStake(
                                netuid=netuid,
                                subnet_name=subnet_name,
                                symbol=symbol,
                                hotkey=hotkey,
                                tao_staked=0,
                                alpha_held=round(alpha_val, 6),
                                alpha_price=round(alpha_price, 8),
                                alpha_value_tao=round(alpha_value_tao, 6),
                                alpha_value_usd=round(alpha_value_usd, 2)
                            ))
                    except Exception:
                        continue

        except Exception as e:
            logger.error(f"Failed to query stakes: {e}")

        return sorted(stakes, key=lambda s: s.netuid)

    def to_dict(self, portfolio: WalletPortfolio) -> dict:
        """Convert WalletPortfolio to dict for JSON serialization."""
        return asdict(portfolio)


# Singleton instance
_wallet_service: Optional[WalletService] = None


def get_wallet_service(cache_ttl: int = 120) -> WalletService:
    """Get or create the Wallet service singleton."""
    global _wallet_service
    if _wallet_service is None:
        _wallet_service = WalletService(cache_ttl=cache_ttl)
    return _wallet_service
