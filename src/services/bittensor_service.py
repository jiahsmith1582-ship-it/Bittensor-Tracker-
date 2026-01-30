"""
Bittensor Blockchain Service

Fetches subnet data from the Bittensor blockchain.
Uses lightweight HTTP JSON-RPC calls to minimize memory usage on free hosting.
Falls back to substrate-interface if available for single-subnet queries.
"""

import gc
import json
import logging
import requests
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

# Try substrate-interface for single-query fallback (not used for batch fetching)
try:
    from async_substrate_interface.sync_substrate import SubstrateInterface
    HAS_SUBSTRATE = True
except ImportError:
    try:
        from substrateinterface import SubstrateInterface
        HAS_SUBSTRATE = True
    except ImportError:
        HAS_SUBSTRATE = False

# Bittensor finney endpoints (HTTP for JSON-RPC, WSS for substrate-interface)
FINNEY_HTTP_ENDPOINTS = [
    "https://entrypoint-finney.opentensor.ai:443",
]

FINNEY_WSS_ENDPOINTS = [
    "wss://entrypoint-finney.opentensor.ai:443",
    "wss://finney.opentensor.ai:443",
]

TESTNET_WSS_ENDPOINTS = [
    "wss://test.finney.opentensor.ai:443",
]

# Subnet names from taostats community data
SUBNET_NAMES_URL = "https://raw.githubusercontent.com/taostat/subnets-infos/main/subnets.json"
_subnet_names: dict = {}


def _fetch_subnet_names() -> dict:
    """Fetch human-readable subnet names from the taostats GitHub repo."""
    global _subnet_names
    if _subnet_names:
        return _subnet_names
    try:
        resp = requests.get(SUBNET_NAMES_URL, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        _subnet_names = {int(k): v for k, v in raw.items()}
        logger.info(f"Loaded {len(_subnet_names)} subnet names")
    except Exception as e:
        logger.warning(f"Failed to fetch subnet names: {e}")
    return _subnet_names


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


# ---------------------------------------------------------------------------
# Lightweight JSON-RPC helper (no substrate-interface needed)
# ---------------------------------------------------------------------------

def _rpc_request(method: str, params: list, endpoint: str = None) -> Optional[dict]:
    """Make a raw JSON-RPC request to a Bittensor node via HTTP POST."""
    if endpoint is None:
        endpoint = FINNEY_HTTP_ENDPOINTS[0]
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    try:
        resp = requests.post(endpoint, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.warning(f"RPC error for {method}: {data['error']}")
            return None
        return data.get("result")
    except Exception as e:
        logger.warning(f"RPC request {method} failed: {e}")
        return None


def _storage_key(pallet: str, storage: str) -> str:
    """Compute the storage key prefix for a pallet+storage using xxhash128."""
    import hashlib
    # Substrate uses xxHash128 for storage keys
    # We'll use the simpler approach: query via state_getKeys
    # Actually, for state_getKeysPaged we need the prefix
    # Let's compute it properly
    try:
        import xxhash
        pallet_hash = xxhash.xxh128(pallet.encode()).hexdigest()
        storage_hash = xxhash.xxh128(storage.encode()).hexdigest()
        return "0x" + pallet_hash + storage_hash
    except ImportError:
        # Fallback: hardcoded keys for known storage functions
        return _KNOWN_STORAGE_KEYS.get(f"{pallet}.{storage}", "")


# Pre-computed storage key prefixes (xxHash128 of pallet + storage names)
# These are deterministic and never change
_KNOWN_STORAGE_KEYS = {}


def _compute_storage_keys():
    """Pre-compute all storage key prefixes we need using pure Python xxhash."""
    global _KNOWN_STORAGE_KEYS
    if _KNOWN_STORAGE_KEYS:
        return

    # xxHash128 implementation for substrate storage keys
    # Substrate uses TwoX128(pallet_name) ++ TwoX128(storage_name)
    try:
        import xxhash
        pallets_storages = [
            ("SubtensorModule", "NetworksAdded"),
            ("SubtensorModule", "SubnetTaoInEmission"),
            ("SubtensorModule", "SubnetMovingPrice"),
            ("SubtensorModule", "SubnetTAO"),
            ("SubtensorModule", "SubnetAlphaIn"),
            ("SubtensorModule", "Tempo"),
            ("SubtensorModule", "SubnetworkN"),
            ("SubtensorModule", "Burn"),
        ]
        for pallet, storage in pallets_storages:
            ph = xxhash.xxh128(pallet.encode()).hexdigest()
            sh = xxhash.xxh128(storage.encode()).hexdigest()
            _KNOWN_STORAGE_KEYS[f"{pallet}.{storage}"] = "0x" + ph + sh
    except ImportError:
        logger.warning("xxhash not available, using hardcoded storage keys")
        # Hardcoded keys (these are deterministic, computed from xxh128)
        _KNOWN_STORAGE_KEYS = {
            "SubtensorModule.NetworksAdded": "0x7769f2d2ca0534ecf5994b7abd13dfd237f540b7f502793c7bfe9cb3f6c0ad64",
            "SubtensorModule.SubnetTaoInEmission": "0x7769f2d2ca0534ecf5994b7abd13dfd23e9ecb6a3dd1eea0a0af2436746847a9",
            "SubtensorModule.SubnetMovingPrice": "0x7769f2d2ca0534ecf5994b7abd13dfd266fa46e411c323890ded4008779d3dc1",
            "SubtensorModule.SubnetTAO": "0x7769f2d2ca0534ecf5994b7abd13dfd253fb8f1d003d137c52679d022eee60e1",
            "SubtensorModule.SubnetAlphaIn": "0x7769f2d2ca0534ecf5994b7abd13dfd25a2d09c7483816a210dcb044050ab521",
            "SubtensorModule.Tempo": "0x7769f2d2ca0534ecf5994b7abd13dfd23d822ed213a59180ae9441dc09b609d9",
            "SubtensorModule.SubnetworkN": "0x7769f2d2ca0534ecf5994b7abd13dfd224371506f995c7cad55d8167796802e0",
            "SubtensorModule.Burn": "0x7769f2d2ca0534ecf5994b7abd13dfd22bbabad73efc1395ca7f42e34095b2ea",
        }


def _query_map_rpc(storage_function: str, endpoint: str = None) -> dict:
    """Query all key-value pairs for a storage function using raw JSON-RPC.

    Uses state_getKeysPaged + state_queryStorageAt for minimal memory usage.
    Returns {netuid: raw_value} dict.
    """
    _compute_storage_keys()
    prefix = _KNOWN_STORAGE_KEYS.get(f"SubtensorModule.{storage_function}", "")
    if not prefix:
        logger.warning(f"No storage key for {storage_function}")
        return {}

    if endpoint is None:
        endpoint = FINNEY_HTTP_ENDPOINTS[0]

    result = {}
    try:
        # Get all keys with this prefix
        all_keys = []
        start_key = prefix
        page_size = 1000
        while True:
            keys = _rpc_request("state_getKeysPaged", [prefix, page_size, start_key], endpoint)
            if not keys:
                break
            all_keys.extend(keys)
            if len(keys) < page_size:
                break
            start_key = keys[-1]

        if not all_keys:
            return {}

        # Query values in batches
        batch_size = 100
        for i in range(0, len(all_keys), batch_size):
            batch_keys = all_keys[i:i + batch_size]
            # Use state_queryStorageAt for batch value retrieval
            storage_result = _rpc_request("state_queryStorageAt", [batch_keys], endpoint)
            if storage_result and isinstance(storage_result, list) and len(storage_result) > 0:
                changes = storage_result[0].get("changes", [])
                for key_hex, value_hex in changes:
                    if value_hex is None:
                        continue
                    # Extract netuid from key (last 2 bytes = u16 little-endian)
                    try:
                        key_bytes = bytes.fromhex(key_hex[2:])  # strip 0x
                        # For u16 keys, the netuid is the last 2 bytes
                        netuid = int.from_bytes(key_bytes[-2:], 'little')
                        # Decode value based on type
                        value = _decode_rpc_value(value_hex, storage_function)
                        result[netuid] = value
                    except Exception as e:
                        logger.debug(f"Failed to decode key/value: {e}")

        gc.collect()

    except Exception as e:
        logger.warning(f"RPC query_map {storage_function} failed: {e}")

    return result


def _decode_rpc_value(hex_value: str, storage_function: str):
    """Decode a hex-encoded storage value based on the storage type."""
    if not hex_value or hex_value == "0x":
        return 0

    raw = bytes.fromhex(hex_value[2:])  # strip 0x

    # Boolean (1 byte)
    if storage_function == "NetworksAdded":
        return bool(raw[0]) if raw else False

    # u64 (8 bytes little-endian) - emissions, TAO reserves, alpha reserves, burn
    if storage_function in ("SubnetTaoInEmission", "SubnetTAO", "SubnetAlphaIn", "Burn"):
        return int.from_bytes(raw[:8], 'little') if len(raw) >= 8 else 0

    # u64 fixed-point for SubnetMovingPrice
    if storage_function == "SubnetMovingPrice":
        return int.from_bytes(raw[:8], 'little') if len(raw) >= 8 else 0

    # u16 (2 bytes) - tempo, neuron count
    if storage_function in ("Tempo", "SubnetworkN"):
        return int.from_bytes(raw[:2], 'little') if len(raw) >= 2 else 0

    # Default: try as u64
    if len(raw) >= 8:
        return int.from_bytes(raw[:8], 'little')
    return int.from_bytes(raw, 'little') if raw else 0


# ---------------------------------------------------------------------------
# Substrate-interface connection (used as fallback for single queries)
# ---------------------------------------------------------------------------

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
    """Service for interacting with the Bittensor blockchain."""

    def __init__(self, network: str = "finney", cache_ttl: int = 300):
        self.network = network
        self.substrate = None
        self._cached_subnets: dict = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = cache_ttl
        self._wss_endpoints = FINNEY_WSS_ENDPOINTS if network == "finney" else TESTNET_WSS_ENDPOINTS
        self._endpoint_index = 0
        self._is_fetching = False
        self._fetch_started: Optional[datetime] = None

    def connect(self) -> bool:
        """Establish connection to the Bittensor network."""
        self.substrate = _create_connection(self._wss_endpoints, self._endpoint_index)
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
        """Fetch all subnets using lightweight HTTP JSON-RPC calls."""
        try:
            logger.info("Fetching all subnets via HTTP JSON-RPC...")
            endpoint = FINNEY_HTTP_ENDPOINTS[0]

            # Step 1: Get active netuids
            networks = _query_map_rpc("NetworksAdded", endpoint)
            netuid_set = set(k for k, v in networks.items() if v)
            del networks
            gc.collect()
            logger.info(f"Found {len(netuid_set)} active subnets")

            if not netuid_set:
                logger.warning("No active subnets found")
                return list(self._cached_subnets.values()) if self._cached_subnets else []

            # Step 2: Emissions
            emissions = _query_map_rpc("SubnetTaoInEmission", endpoint)
            total_emission = sum(float(emissions.get(n, 0)) for n in netuid_set)
            data = {}
            for n in netuid_set:
                em = float(emissions.get(n, 0))
                data[n] = {'em': em, 'em_pct': (em / total_emission * 100) if total_emission > 0 else 0}
            del emissions
            gc.collect()

            # Step 3: Fetch remaining fields one at a time
            for field, storage in [
                ('price', 'SubnetMovingPrice'),
                ('tao_r', 'SubnetTAO'),
                ('alpha_r', 'SubnetAlphaIn'),
                ('tempo', 'Tempo'),
                ('neurons', 'SubnetworkN'),
                ('burn', 'Burn'),
            ]:
                raw = _query_map_rpc(storage, endpoint)
                for n in netuid_set:
                    data[n][field] = raw.get(n, 0)
                del raw
                gc.collect()
                logger.info(f"Fetched {field}")

            # Fetch human-readable subnet names (small HTTP request)
            subnet_names = _fetch_subnet_names()

            # Build SubnetInfo objects
            subnets = []
            now = datetime.now().isoformat()
            for netuid in sorted(netuid_set):
                try:
                    d = data[netuid]
                    raw_price = d['price']

                    tao_in = _rao_to_tao(d['tao_r'])
                    name = subnet_names.get(netuid, f"Subnet {netuid}")

                    subnets.append(SubnetInfo(
                        netuid=netuid,
                        name=name,
                        symbol=f"SN{netuid}",
                        owner="",
                        emission=round(_rao_to_tao(d['em']), 6),
                        emission_percentage=round(d['em_pct'], 4),
                        tempo=int(d.get('tempo', 0)),
                        neurons=int(d.get('neurons', 0)),
                        registration_cost=round(_rao_to_tao(d['burn']), 4),
                        alpha_price=round(_decode_fixed_point(raw_price, 32), 8),
                        tao_in_reserve=round(tao_in, 4),
                        alpha_in_reserve=round(_rao_to_tao(d['alpha_r']), 4),
                        subnet_tao=round(tao_in, 4),
                        timestamp=now
                    ))
                except Exception as e:
                    logger.warning(f"Failed to build subnet {netuid}: {e}")

            del data
            gc.collect()

            # Update cache
            self._cached_subnets = {s.netuid: s for s in subnets}
            self._cache_timestamp = datetime.now()

            logger.info(f"Successfully fetched {len(subnets)} subnets")
            return subnets

        except Exception as e:
            logger.error(f"Failed to fetch subnets: {e}")
            if self._cached_subnets:
                return list(self._cached_subnets.values())
            return []

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
        # Try from cache via full fetch
        subnets = self.get_all_subnets()
        return self._cached_subnets.get(netuid)

    def get_current_block(self) -> int:
        """Get the current block number via JSON-RPC."""
        result = _rpc_request("chain_getHeader", [])
        if result and "number" in result:
            return int(result["number"], 16)
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
