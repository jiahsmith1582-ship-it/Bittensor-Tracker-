"""
Bittensor Blockchain Service

Fetches subnet data from the Bittensor blockchain.
Uses lightweight HTTP JSON-RPC calls to minimize memory usage on free hosting.
Falls back to substrate-interface if available for single-subnet queries.
"""

import gc
import logging
import time
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


# Pre-computed storage key prefixes for SubtensorModule storage functions.
# Substrate TwoX128 = xxh64(data, seed=0) LE || xxh64(data, seed=1) LE
# Key = TwoX128(pallet_name) ++ TwoX128(storage_name)
_KNOWN_STORAGE_KEYS = {
        "SubtensorModule.NetworksAdded": "0x658faa385070e074c85bf6b568cf05550e30450fc4d507a846032a7fa65d9a43",
        "SubtensorModule.SubnetTaoInEmission": "0x658faa385070e074c85bf6b568cf0555dd62ae7237581e8f6a684f1ecae06215",
        "SubtensorModule.SubnetMovingPrice": "0x658faa385070e074c85bf6b568cf05551abf1b0f4fd14f7b72ee50f9d91d5915",
        "SubtensorModule.SubnetTAO": "0x658faa385070e074c85bf6b568cf05557a57dce016211512d1700561066b85a3",
        "SubtensorModule.SubnetAlphaIn": "0x658faa385070e074c85bf6b568cf05552ce12f7007574647d692ac7edf8b7a53",
        "SubtensorModule.Tempo": "0x658faa385070e074c85bf6b568cf05557641384bb339f3758acddfd7053d3317",
        "SubtensorModule.SubnetworkN": "0x658faa385070e074c85bf6b568cf0555a1048e9d244171852dfe8db314dc68ca",
        "SubtensorModule.Burn": "0x658faa385070e074c85bf6b568cf055501be1755d08418802946bca51b686325",
    }


def _query_map_rpc(storage_function: str, endpoint: str = None, retries: int = 2) -> dict:
    """Query all key-value pairs for a storage function using raw JSON-RPC.

    Uses state_getKeysPaged + state_queryStorageAt for minimal memory usage.
    Returns {netuid: raw_value} dict.
    """
    prefix = _KNOWN_STORAGE_KEYS.get(f"SubtensorModule.{storage_function}", "")
    if not prefix:
        logger.warning(f"No storage key for {storage_function}")
        return {}

    if endpoint is None:
        endpoint = FINNEY_HTTP_ENDPOINTS[0]

    for attempt in range(retries + 1):
        result = {}
        try:
            # Get all keys with this prefix
            all_keys = []
            start_key = None
            page_size = 1000
            while True:
                params = [prefix, page_size] if start_key is None else [prefix, page_size, start_key]
                keys = _rpc_request("state_getKeysPaged", params, endpoint)
                if not keys:
                    break
                all_keys.extend(keys)
                if len(keys) < page_size:
                    break
                start_key = keys[-1]

            if not all_keys:
                if attempt < retries:
                    time.sleep(2)
                    continue
                return {}

            # Query values in batches
            batch_size = 50
            for i in range(0, len(all_keys), batch_size):
                batch_keys = all_keys[i:i + batch_size]
                storage_result = _rpc_request("state_queryStorageAt", [batch_keys], endpoint)
                if storage_result and isinstance(storage_result, list) and len(storage_result) > 0:
                    changes = storage_result[0].get("changes", [])
                    for key_hex, value_hex in changes:
                        if value_hex is None:
                            continue
                        try:
                            key_bytes = bytes.fromhex(key_hex[2:])
                            netuid = int.from_bytes(key_bytes[-2:], 'little')
                            value = _decode_rpc_value(value_hex, storage_function)
                            result[netuid] = value
                        except Exception as e:
                            logger.debug(f"Failed to decode key/value: {e}")
                # Small delay between batches to avoid rate limiting
                if i + batch_size < len(all_keys):
                    time.sleep(0.2)

            gc.collect()

            if result:
                return result

            # Got keys but no values — likely rate-limited, retry
            if attempt < retries:
                logger.warning(f"RPC query_map {storage_function} returned empty values, retrying ({attempt+1}/{retries})...")
                time.sleep(3)

        except Exception as e:
            logger.warning(f"RPC query_map {storage_function} failed (attempt {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(3)

    return result


def _build_storage_key(prefix_hex: str, netuid: int) -> str:
    """Build a full storage key (with 0x) for a u16 netuid."""
    return "0x" + prefix_hex + netuid.to_bytes(2, 'little').hex()


def _query_combined_rpc(netuids: set, storage_fields: list, endpoint: str) -> dict:
    """Query multiple storage functions for known netuids using batch RPC.

    Constructs exact storage keys from known netuids and queries values using
    state_queryStorageAt in small batches (30 keys each). This keeps both
    the number of HTTP calls (~5 per field) and response size small.
    Returns {field_name: {netuid: value}}.
    """
    result = {field: {} for field, _ in storage_fields}

    for field, storage in storage_fields:
        prefix = _KNOWN_STORAGE_KEYS.get(f"SubtensorModule.{storage}", "")
        if not prefix:
            continue
        prefix_hex = prefix[2:]

        # Build keys for all netuids
        keys_with_netuid = []
        for netuid in sorted(netuids):
            key_hex = _build_storage_key(prefix_hex, netuid)
            keys_with_netuid.append((key_hex, netuid))

        # Query in small batches of 30
        for i in range(0, len(keys_with_netuid), 30):
            batch = keys_with_netuid[i:i + 30]
            batch_keys = [k for k, _ in batch]

            for attempt in range(3):
                try:
                    payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "state_queryStorageAt",
                        "params": [batch_keys]
                    }
                    resp = requests.post(endpoint, json=payload, timeout=30)
                    data = resp.json()
                    if "error" in data:
                        logger.warning(f"RPC error {storage}: {data['error']}")
                        time.sleep(2)
                        continue
                    sr = data.get("result")
                    if sr and isinstance(sr, list) and len(sr) > 0:
                        changes = {k: v for k, v in sr[0].get("changes", []) if v}
                        for key_hex, netuid in batch:
                            val = changes.get(key_hex)
                            if val:
                                result[field][netuid] = _decode_rpc_value(val, storage)
                        break
                except Exception as e:
                    logger.warning(f"Batch {storage}[{i}] failed: {e}")
                time.sleep(2)

            time.sleep(0.5)

        logger.info(f"Fetched {field}: {len(result[field])} non-zero")
        gc.collect()
        time.sleep(1)

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
            if self._fetch_started and (datetime.now() - self._fetch_started).total_seconds() > 300:
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

            # Step 3: Fetch all remaining fields in one combined batch
            # Build keys for all storage functions and all netuids at once
            storage_fields = [
                ('price', 'SubnetMovingPrice'),
                ('tao_r', 'SubnetTAO'),
                ('alpha_r', 'SubnetAlphaIn'),
                ('tempo', 'Tempo'),
                ('neurons', 'SubnetworkN'),
                ('burn', 'Burn'),
            ]
            combined = _query_combined_rpc(netuid_set, storage_fields, endpoint)
            for n in netuid_set:
                for field, _ in storage_fields:
                    data[n][field] = combined.get(field, {}).get(n, 0)
            del combined
            gc.collect()
            for field, _ in storage_fields:
                non_zero = len([n for n in netuid_set if data[n][field] != 0])
                logger.info(f"Fetched {field} ({non_zero} non-zero)")

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
                    raw_name = subnet_names.get(netuid, f"Subnet {netuid}")
                    name = raw_name.get("name", str(raw_name)) if isinstance(raw_name, dict) else str(raw_name)

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
        self.get_all_subnets()
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
