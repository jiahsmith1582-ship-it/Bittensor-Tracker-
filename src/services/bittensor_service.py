"""
Bittensor Blockchain Service

Fetches subnet data from the Bittensor blockchain using async-substrate-interface.
This approach works natively on Windows without requiring the bittensor SDK
(which has Rust/Unix-only dependencies).
"""

import logging
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from async_substrate_interface.sync_substrate import SubstrateInterface
    HAS_SUBSTRATE = True
except ImportError:
    try:
        from substrateinterface import SubstrateInterface
        HAS_SUBSTRATE = True
    except ImportError:
        HAS_SUBSTRATE = False
        logger.warning("substrate-interface not installed. Run: pip install async-substrate-interface")


# Bittensor finney endpoints
FINNEY_ENDPOINTS = [
    "wss://entrypoint-finney.opentensor.ai:443",
    "wss://finney.opentensor.ai:443",
]

TESTNET_ENDPOINTS = [
    "wss://test.finney.opentensor.ai:443",
]


@dataclass
class SubnetInfo:
    """Data class for subnet information."""
    netuid: int
    name: str
    symbol: str
    owner: str
    emission: float
    emission_percentage: float
    tempo: int
    neurons: int
    registration_cost: float
    alpha_price: float       # TAO per alpha token
    tao_in_reserve: float    # TAO in the subnet's reserve pool
    alpha_in_reserve: float  # Alpha tokens in the subnet's reserve pool
    subnet_tao: float        # Total TAO in subnet
    timestamp: str


def _rao_to_tao(rao_value) -> float:
    """Convert rao (raw blockchain units) to TAO. 1 TAO = 1e9 rao."""
    val = float(rao_value or 0)
    return val / 1e9


def _decode_fixed_point(raw_value, fractional_bits: int = 32) -> float:
    """Decode a fixed-point integer from the blockchain."""
    val = int(raw_value or 0)
    if val == 0:
        return 0.0
    return val / (2 ** fractional_bits)


def _create_connection(endpoints, endpoint_index=0):
    """Create a new SubstrateInterface connection."""
    if not HAS_SUBSTRATE:
        return None
    for i in range(len(endpoints)):
        endpoint = endpoints[(endpoint_index + i) % len(endpoints)]
        try:
            logger.info(f"Connecting to {endpoint}...")
            substrate = SubstrateInterface(url=endpoint)
            logger.info(f"Connected to {endpoint}")
            return substrate
        except Exception as e:
            logger.warning(f"Failed to connect to {endpoint}: {e}")
    return None


class BittensorService:
    """Service for interacting with the Bittensor blockchain via substrate-interface."""

    def __init__(self, network: str = "finney", cache_ttl: int = 300):
        self.network = network
        self.substrate = None
        self._cached_subnets: dict = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = cache_ttl
        self._endpoints = FINNEY_ENDPOINTS if network == "finney" else TESTNET_ENDPOINTS
        self._endpoint_index = 0
        self._is_fetching = False
        self._fetch_started: Optional[datetime] = None

    def connect(self) -> bool:
        """Establish connection to the Bittensor network."""
        self.substrate = _create_connection(self._endpoints, self._endpoint_index)
        return self.substrate is not None

    def _ensure_connected(self) -> bool:
        """Ensure we have a connection, reconnecting if needed."""
        if self.substrate is None:
            return self.connect()
        try:
            self.substrate.get_block_number(None)
            return True
        except Exception:
            logger.info("Connection lost, reconnecting...")
            self.substrate = None
            return self.connect()

    def get_all_subnets(self, use_cache: bool = True) -> list[SubnetInfo]:
        """Fetch information for all subnets."""
        # Check cache
        if use_cache and self._cached_subnets and self._cache_timestamp:
            cache_age = (datetime.now() - self._cache_timestamp).total_seconds()
            if cache_age < self._cache_ttl_seconds:
                return list(self._cached_subnets.values())

        # If another fetch is running, return cache (with 30 min timeout to auto-reset)
        if self._is_fetching:
            if self._fetch_started and (datetime.now() - self._fetch_started).total_seconds() > 1800:
                logger.warning("Fetch seems stuck (>30 min), resetting flag")
                self._is_fetching = False
            else:
                return list(self._cached_subnets.values()) if self._cached_subnets else []

        self._is_fetching = True
        self._fetch_started = datetime.now()
        try:
            return self._do_fetch_all()
        finally:
            self._is_fetching = False

    def _do_fetch_all(self) -> list[SubnetInfo]:
        """Fetch all subnets using batch query_map calls (~20s instead of ~20min)."""
        substrate = _create_connection(self._endpoints, self._endpoint_index)
        if not substrate:
            logger.error("Cannot connect to any endpoint")
            return list(self._cached_subnets.values()) if self._cached_subnets else []

        try:
            logger.info("Fetching all subnets (batch mode)...")

            def query_map_to_dict(storage_function):
                """Batch-fetch all values for a storage function."""
                result = {}
                try:
                    qm = substrate.query_map(
                        module="SubtensorModule",
                        storage_function=storage_function
                    )
                    for key, val in qm:
                        k = int(key.value if hasattr(key, 'value') else key)
                        v = val.value if hasattr(val, 'value') else val
                        result[k] = v
                except Exception as e:
                    logger.warning(f"query_map {storage_function} failed: {e}")
                return result

            # Batch fetch all data (~2s per query_map call)
            networks = query_map_to_dict("NetworksAdded")
            netuid_list = [k for k, v in networks.items() if v]
            logger.info(f"Found {len(netuid_list)} active subnets")

            emissions = query_map_to_dict("SubnetTaoInEmission")
            prices = query_map_to_dict("SubnetMovingPrice")
            tao_reserves = query_map_to_dict("SubnetTAO")
            alpha_reserves = query_map_to_dict("SubnetAlphaIn")
            tempos = query_map_to_dict("Tempo")
            neuron_counts = query_map_to_dict("SubnetworkN")
            burns = query_map_to_dict("Burn")
            owners = query_map_to_dict("SubnetOwner")
            symbols = query_map_to_dict("TokenSymbol")

            total_emission = sum(float(emissions.get(n, 0)) for n in netuid_list)

            # Build SubnetInfo objects from batch data
            subnets = []
            now = datetime.now().isoformat()
            for netuid in sorted(netuid_list):
                try:
                    em = float(emissions.get(netuid, 0))
                    em_pct = (em / total_emission * 100) if total_emission > 0 else 0

                    raw_price = prices.get(netuid, 0)
                    if isinstance(raw_price, dict):
                        raw_price = raw_price.get('bits', 0)
                    alpha_price = _decode_fixed_point(raw_price, 32)

                    tao_in = _rao_to_tao(tao_reserves.get(netuid, 0))
                    alpha_in = _rao_to_tao(alpha_reserves.get(netuid, 0))
                    burn = _rao_to_tao(burns.get(netuid, 0))

                    symbol_raw = symbols.get(netuid)
                    symbol = self._decode_bytes(symbol_raw) or f"SN{netuid}"

                    subnets.append(SubnetInfo(
                        netuid=netuid,
                        name=f"Subnet {netuid}",
                        symbol=symbol,
                        owner=str(owners.get(netuid, "Unknown")),
                        emission=round(_rao_to_tao(em), 6),
                        emission_percentage=round(em_pct, 4),
                        tempo=int(tempos.get(netuid, 0)),
                        neurons=int(neuron_counts.get(netuid, 0)),
                        registration_cost=round(burn, 4),
                        alpha_price=round(alpha_price, 8),
                        tao_in_reserve=round(tao_in, 4),
                        alpha_in_reserve=round(alpha_in, 4),
                        subnet_tao=round(tao_in, 4),
                        timestamp=now
                    ))
                except Exception as e:
                    logger.warning(f"Failed to build subnet {netuid}: {e}")

            # Update cache
            self._cached_subnets = {s.netuid: s for s in subnets}
            self._cache_timestamp = datetime.now()
            self.substrate = substrate

            logger.info(f"Successfully fetched {len(subnets)} subnets")
            return subnets

        except Exception as e:
            logger.error(f"Failed to fetch subnets: {e}")
            if self._cached_subnets:
                return list(self._cached_subnets.values())
            return []

    def _fetch_subnet_with(self, substrate, netuid: int, emission: float,
                           total_emission: float) -> Optional[SubnetInfo]:
        """Fetch data for a single subnet using the provided connection."""
        emission_pct = (emission / total_emission * 100) if total_emission > 0 else 0

        def qv(storage_function, params=None):
            """Query a value from the blockchain."""
            try:
                result = substrate.query(
                    module="SubtensorModule",
                    storage_function=storage_function,
                    params=params or []
                )
                if result is None:
                    return None
                return result.value if hasattr(result, 'value') else result
            except Exception:
                return None

        owner = qv("SubnetOwner", [netuid]) or "Unknown"
        tempo = qv("Tempo", [netuid]) or 0
        neurons = qv("SubnetworkN", [netuid]) or 0

        # Alpha price
        raw_price = qv("SubnetMovingPrice", [netuid])
        if isinstance(raw_price, dict):
            raw_price = raw_price.get('bits', 0)
        alpha_price = _decode_fixed_point(raw_price, 32)

        # Reserve pool data
        tao_in = _rao_to_tao(qv("SubnetTAO", [netuid]))
        alpha_in = _rao_to_tao(qv("SubnetAlphaIn", [netuid]))

        # Registration cost
        burn = _rao_to_tao(qv("Burn", [netuid]))

        # Subnet name
        name_raw = None
        for name_key in ["SubnetName", "NetworksName", "SubnetIdentity"]:
            name_raw = qv(name_key, [netuid])
            if name_raw:
                break

        # Token symbol
        symbol_raw = qv("TokenSymbol", [netuid])

        name = self._decode_bytes(name_raw) or f"Subnet {netuid}"
        symbol = self._decode_bytes(symbol_raw) or f"SN{netuid}"

        return SubnetInfo(
            netuid=netuid,
            name=name,
            symbol=symbol,
            owner=str(owner),
            emission=round(_rao_to_tao(emission), 6),
            emission_percentage=round(emission_pct, 4),
            tempo=int(tempo or 0),
            neurons=int(neurons or 0),
            registration_cost=round(burn, 4),
            alpha_price=round(alpha_price, 8),
            tao_in_reserve=round(tao_in, 4),
            alpha_in_reserve=round(alpha_in, 4),
            subnet_tao=round(tao_in, 4),
            timestamp=datetime.now().isoformat()
        )

    # Keep old method name for compatibility with wallet_service
    def _fetch_subnet(self, netuid: int, emission: float, total_emission: float) -> Optional[SubnetInfo]:
        if self.substrate is None:
            self.connect()
        return self._fetch_subnet_with(self.substrate, netuid, emission, total_emission)

    def _query(self, module: str, storage_function: str, params=None):
        """Execute a substrate query."""
        try:
            if params:
                return self.substrate.query(
                    module=module,
                    storage_function=storage_function,
                    params=params
                )
            else:
                return self.substrate.query_map(
                    module=module,
                    storage_function=storage_function
                )
        except Exception as e:
            logger.debug(f"Query {module}.{storage_function} failed: {e}")
            return None

    def _query_value(self, module: str, storage_function: str, params=None):
        """Execute a substrate query and return the raw value."""
        try:
            result = self.substrate.query(
                module=module,
                storage_function=storage_function,
                params=params or []
            )
            if result is None:
                return None
            return result.value if hasattr(result, 'value') else result
        except Exception as e:
            logger.debug(f"Query {module}.{storage_function}({params}) failed: {e}")
            return None

    def _decode_bytes(self, raw) -> str:
        """Decode a bytes value from the blockchain into a string."""
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode('utf-8', errors='replace').strip('\x00')
        if isinstance(raw, list):
            return bytes(raw).decode('utf-8', errors='replace').strip('\x00')
        return str(raw)

    def get_subnet(self, netuid: int) -> Optional[SubnetInfo]:
        """Fetch information for a specific subnet."""
        if netuid in self._cached_subnets:
            return self._cached_subnets[netuid]

        if not self._ensure_connected():
            return None

        try:
            emission = float(self._query_value("SubtensorModule", "SubnetTaoInEmission", [netuid]) or 0)
            return self._fetch_subnet(netuid, emission, emission or 1)
        except Exception as e:
            logger.error(f"Failed to fetch subnet {netuid}: {e}")
            return None

    def get_current_block(self) -> int:
        """Get the current block number."""
        if not self._ensure_connected():
            return 0
        try:
            return self.substrate.get_block_number(None) or 0
        except Exception as e:
            logger.error(f"Failed to get current block: {e}")
            return 0

    def get_subnet_by_netuid(self, netuid: int) -> Optional[SubnetInfo]:
        """Get cached subnet info by netuid, fetching all if cache is empty."""
        if not self._cached_subnets:
            self.get_all_subnets()
        return self._cached_subnets.get(netuid)

    def to_dict_list(self, subnets: list[SubnetInfo]) -> list[dict]:
        """Convert list of SubnetInfo to list of dicts for JSON serialization."""
        return [asdict(s) for s in subnets]


# Singleton instance
_service: Optional[BittensorService] = None


def get_bittensor_service(network: str = "finney", cache_ttl: int = 300) -> BittensorService:
    """Get or create the Bittensor service singleton."""
    global _service
    if _service is None:
        _service = BittensorService(network=network, cache_ttl=cache_ttl)
    return _service
