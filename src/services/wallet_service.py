"""
Wallet Portfolio Service

Queries the Taostats API for wallet portfolio data including
TAO balance and per-subnet alpha stakes.
"""

import logging
import requests
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime

from ..config import config
from .bittensor_service import get_bittensor_service

logger = logging.getLogger(__name__)

TAOSTATS_BASE = "https://api.taostats.io/api"


def _rao_to_tao(rao_str) -> float:
    """Convert rao string to TAO float."""
    return float(rao_str or 0) / 1e9


@dataclass
class SubnetStake:
    netuid: int
    subnet_name: str
    symbol: str
    hotkey: str
    alpha_held: float
    alpha_value_tao: float

@dataclass
class WalletPortfolio:
    coldkey: str
    free_balance_tao: float
    total_staked_tao: float
    total_portfolio_tao: float
    subnet_stakes: list
    timestamp: str


class WalletService:

    def __init__(self, cache_ttl: int = 120):
        self._cache: dict[str, WalletPortfolio] = {}
        self._cache_timestamps: dict[str, datetime] = {}
        self._cache_ttl_seconds = cache_ttl

    def get_portfolio(self, coldkey_ss58: str, use_cache: bool = True) -> Optional[WalletPortfolio]:
        if use_cache and coldkey_ss58 in self._cache:
            cache_ts = self._cache_timestamps.get(coldkey_ss58)
            if cache_ts:
                age = (datetime.now() - cache_ts).total_seconds()
                if age < self._cache_ttl_seconds:
                    return self._cache[coldkey_ss58]

        try:
            api_key = config.TAOSTATS_API_KEY
            if not api_key:
                logger.error("TAOSTATS_API_KEY not configured")
                return None

            resp = requests.get(
                f"{TAOSTATS_BASE}/account/latest/v1",
                headers={"Authorization": api_key},
                params={"address": coldkey_ss58},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            records = data.get("data", [])
            if not records:
                logger.warning(f"No account data for {coldkey_ss58[:12]}...")
                return None

            acct = records[0]

            free_balance = _rao_to_tao(acct.get("balance_free", 0))
            total_staked = _rao_to_tao(acct.get("balance_staked", 0))
            total_portfolio = _rao_to_tao(acct.get("balance_total", 0))

            # Build per-subnet stakes from alpha_balances
            bt_service = get_bittensor_service()
            stakes = []
            for ab in acct.get("alpha_balances", []):
                netuid = ab.get("netuid", 0)
                alpha_held = _rao_to_tao(ab.get("balance", 0))
                alpha_as_tao = _rao_to_tao(ab.get("balance_as_tao", 0))
                hotkey = ab.get("hotkey", "")

                subnet_info = bt_service.get_subnet(netuid)
                raw_name = subnet_info.name if subnet_info else f"Subnet {netuid}"
                subnet_name = raw_name.get("name", str(raw_name)) if isinstance(raw_name, dict) else str(raw_name)
                symbol = subnet_info.symbol if subnet_info else f"SN{netuid}"

                stakes.append(SubnetStake(
                    netuid=netuid,
                    subnet_name=subnet_name,
                    symbol=symbol,
                    hotkey=hotkey,
                    alpha_held=round(alpha_held, 6),
                    alpha_value_tao=round(alpha_as_tao, 6),
                ))

            stakes.sort(key=lambda s: s.netuid)

            portfolio = WalletPortfolio(
                coldkey=coldkey_ss58,
                free_balance_tao=round(free_balance, 6),
                total_staked_tao=round(total_staked, 6),
                total_portfolio_tao=round(total_portfolio, 6),
                subnet_stakes=[asdict(s) for s in stakes],
                timestamp=acct.get("timestamp", datetime.now().isoformat()),
            )

            self._cache[coldkey_ss58] = portfolio
            self._cache_timestamps[coldkey_ss58] = datetime.now()
            return portfolio

        except Exception as e:
            logger.error(f"Failed to get portfolio for {coldkey_ss58[:12]}...: {e}")
            return None

    def get_transfers(self, coldkey_ss58: str, limit: int = 50) -> list[dict]:
        """Get recent TAO transfers for a coldkey."""
        try:
            api_key = config.TAOSTATS_API_KEY
            if not api_key:
                return []
            resp = requests.get(
                f"{TAOSTATS_BASE}/transfer/v1",
                headers={"Authorization": api_key},
                params={"address": coldkey_ss58, "limit": limit},
                timeout=15
            )
            resp.raise_for_status()
            rows = []
            for t in resp.json().get("data", []):
                from_addr = t.get("from", {}).get("ss58", "")
                to_addr = t.get("to", {}).get("ss58", "")
                if to_addr == coldkey_ss58:
                    direction = "Receive"
                elif from_addr == coldkey_ss58:
                    direction = "Send"
                else:
                    direction = "Unknown"
                rows.append({
                    "block": t.get("block_number", 0),
                    "timestamp": t.get("timestamp", ""),
                    "direction": direction,
                    "from": from_addr,
                    "to": to_addr,
                    "amount_tao": round(_rao_to_tao(t.get("amount", 0)), 6),
                    "fee_tao": round(_rao_to_tao(t.get("fee", 0)), 9),
                    "extrinsic_id": t.get("extrinsic_id", ""),
                })
            return rows
        except Exception as e:
            logger.error(f"Failed to get transfers: {e}")
            return []

    def get_delegations(self, coldkey_ss58: str) -> list[dict]:
        """Get all delegation (stake/unstake) events for a coldkey via pagination."""
        try:
            api_key = config.TAOSTATS_API_KEY
            if not api_key:
                return []

            bt_service = get_bittensor_service()
            rows = []
            page = 1
            page_size = 200

            while True:
                resp = requests.get(
                    f"{TAOSTATS_BASE}/delegation/v1",
                    headers={"Authorization": api_key},
                    params={"nominator": coldkey_ss58, "limit": page_size, "page": page},
                    timeout=30
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])

                if not data:
                    break

                for d in data:
                    netuid = d.get("netuid", 0)
                    subnet_info = bt_service.get_subnet(netuid)
                    subnet_name = ""
                    if subnet_info:
                        raw_name = subnet_info.name
                        subnet_name = raw_name.get("name", str(raw_name)) if isinstance(raw_name, dict) else str(raw_name)
                    else:
                        subnet_name = f"Subnet {netuid}"
                    action = d.get("action", "")
                    if action == "DELEGATE":
                        action = "Buy"
                    elif action == "UNDELEGATE":
                        action = "Sell"
                    symbol = subnet_info.symbol if subnet_info else f"SN{netuid}"
                    rows.append({
                        "block": d.get("block_number", 0),
                        "timestamp": d.get("timestamp", ""),
                        "action": action,
                        "netuid": netuid,
                        "subnet_name": subnet_name,
                        "symbol": symbol,
                        "delegate_name": d.get("delegate_name", ""),
                        "delegate": d.get("delegate", {}).get("ss58", ""),
                        "amount_tao": round(_rao_to_tao(d.get("amount", 0)), 6),
                        "alpha": round(_rao_to_tao(d.get("alpha", 0)), 6),
                        "alpha_price_tao": d.get("alpha_price_in_tao", "0"),
                        "extrinsic_id": d.get("extrinsic_id", ""),
                    })

                if len(data) < page_size:
                    break
                page += 1

            return rows
        except Exception as e:
            logger.error(f"Failed to get delegations: {e}")
            return []

    def to_dict(self, portfolio: WalletPortfolio) -> dict:
        return asdict(portfolio)


    def get_whale_transactions(self, limit_per_whale: int = 10) -> list[dict]:
        """Get recent delegation transactions from top whale wallets."""
        import time

        WHALE_ADDRESSES = [
            "5Hd2ze5ug8n1bo3UCAcQsf66VNjKqGos8u6apNfzcU86pg4N",
            "5GH2aUTMRUh1RprCgH4x3tRyCaKeUi5BfmYCfs1NARA8R54n",
            "5GaTCa1ZEBTjfiSq9965mvaDkc8YgWnsHzrdVXMmmzWDy3iJ",
            "5Gs1zPdtVKJ9dh1DdTRYrNPbxnkBmLCBPQ5FVMjn7mV1kUNA",
            "5HbUBhvvCVsYYA1ghsHrVzSzARxhkF5yLFMctVgcENXjJ329",
            "5HiveMEoWPmQmBAb8v63bKPcFhgTGCmST1TVZNvPHSTKFLCv",
            "5FqBL928choLPmeFz5UVAvonBD5k7K2mZSXVC9RkFzLxoy2s",
            "5HeWF2M4hHtsjeaKemfVUi427sM6caTGyqZo5XvCQLE8r7qc",
            "5FRqwexkxqXLg4frDhGKM6rCw2y32JWojQ6JV6zBgzhnhq7r",
            "5ELWnR5A7DUmmqHsYPA3iZMFu1BX3gceruEqFPtsmTkCqR7J",
        ]

        # Check cache
        cache_key = "_whales_"
        if cache_key in self._cache_timestamps:
            age = (datetime.now() - self._cache_timestamps[cache_key]).total_seconds()
            if age < 600 and cache_key in self._cache:
                return self._cache[cache_key]

        try:
            api_key = config.TAOSTATS_API_KEY
            if not api_key:
                return []

            bt_service = get_bittensor_service()
            all_rows = []

            for whale in WHALE_ADDRESSES:
                try:
                    resp = requests.get(
                        f"{TAOSTATS_BASE}/delegation/v1",
                        headers={"Authorization": api_key},
                        params={"nominator": whale, "limit": limit_per_whale},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    short_whale = whale[:8] + "..." + whale[-6:]

                    for d in resp.json().get("data", []):
                        netuid = d.get("netuid", 0)
                        subnet_info = bt_service.get_subnet(netuid)
                        subnet_name = ""
                        if subnet_info:
                            raw_name = subnet_info.name
                            subnet_name = raw_name.get("name", str(raw_name)) if isinstance(raw_name, dict) else str(raw_name)
                        else:
                            subnet_name = f"Subnet {netuid}"
                        action = d.get("action", "")
                        if action == "DELEGATE":
                            action = "Buy"
                        elif action == "UNDELEGATE":
                            action = "Sell"
                        symbol = subnet_info.symbol if subnet_info else f"SN{netuid}"
                        all_rows.append({
                            "whale": short_whale,
                            "timestamp": d.get("timestamp", ""),
                            "action": action,
                            "netuid": netuid,
                            "subnet_name": subnet_name,
                            "symbol": symbol,
                            "delegate_name": d.get("delegate_name", ""),
                            "amount_tao": round(_rao_to_tao(d.get("amount", 0)), 6),
                            "alpha": round(_rao_to_tao(d.get("alpha", 0)), 6),
                            "alpha_price_tao": d.get("alpha_price_in_tao", "0"),
                        })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"Failed to fetch whale {whale[:12]}...: {e}")
                    continue

            self._cache[cache_key] = all_rows
            self._cache_timestamps[cache_key] = datetime.now()
            return all_rows

        except Exception as e:
            logger.error(f"Failed to get whale transactions: {e}")
            return []


_wallet_service: Optional[WalletService] = None


def get_wallet_service(cache_ttl: int = 120) -> WalletService:
    global _wallet_service
    if _wallet_service is None:
        _wallet_service = WalletService(cache_ttl=cache_ttl)
    return _wallet_service
