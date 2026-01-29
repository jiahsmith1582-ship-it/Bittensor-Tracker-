"""
Subnet Data Fetcher CLI

A simple script to test fetching subnet data from the Bittensor blockchain.
Use this to verify your bittensor installation is working.

Usage:
    python fetch_subnets.py           # Fetch all subnets
    python fetch_subnets.py --netuid 1 # Fetch specific subnet
    python fetch_subnets.py --price   # Fetch TAO price only
    python fetch_subnets.py --wallet 5Cai... # Fetch wallet portfolio
"""

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_subnets(netuid: int = None):
    """Fetch subnet data from the blockchain."""
    from src.services.bittensor_service import get_bittensor_service

    print("\nConnecting to Bittensor network...")
    service = get_bittensor_service()

    if not service.connect():
        print("Failed to connect to Bittensor network!")
        print("Make sure bittensor is installed: pip install bittensor")
        return

    print(f"Connected! Current block: {service.get_current_block()}\n")

    if netuid is not None:
        print(f"Fetching subnet {netuid}...")
        subnet = service.get_subnet(netuid)
        if subnet:
            print(json.dumps(service.to_dict_list([subnet])[0], indent=2))
        else:
            print(f"Subnet {netuid} not found")
    else:
        print("Fetching all subnets (this may take a moment)...")
        subnets = service.get_all_subnets(use_cache=False)
        print(f"\nFound {len(subnets)} subnets:\n")
        print("-" * 110)
        print(f"{'NetUID':<8} {'Name':<20} {'Symbol':<10} {'Emission %':<12} {'Alpha Price':<14} {'TAO Reserve':<12} {'Neurons':<10}")
        print("-" * 110)

        for s in sorted(subnets, key=lambda x: x.netuid):
            name = s.name[:19].encode('ascii', 'replace').decode('ascii')
            symbol = s.symbol.encode('ascii', 'replace').decode('ascii')
            print(f"{s.netuid:<8} {name:<20} {symbol:<10} {s.emission_percentage:<12.4f} {s.alpha_price:<14.8f} {s.tao_in_reserve:<12.4f} {s.neurons:<10}")

        print("-" * 110)


def fetch_price():
    """Fetch TAO price."""
    from src.services.price_service import get_price_service

    print("\nFetching TAO price...")
    service = get_price_service()
    price = service.get_tao_price()

    if price:
        print(f"\nTAO Price: ${price.price_usd:.2f} USD")
        if price.change_24h_percent:
            print(f"24h Change: {price.change_24h_percent:.2f}%")
        if price.market_cap_usd:
            print(f"Market Cap: ${price.market_cap_usd:,.0f}")
        if price.volume_24h_usd:
            print(f"24h Volume: ${price.volume_24h_usd:,.0f}")
        print(f"Source: {price.source}")
        print(f"Timestamp: {price.timestamp}")
    else:
        print("Failed to fetch TAO price")


def fetch_wallet(address: str):
    """Fetch wallet portfolio data."""
    from src.services.wallet_service import get_wallet_service

    print(f"\nFetching portfolio for {address[:12]}...")
    service = get_wallet_service()
    portfolio = service.get_portfolio(address)

    if not portfolio:
        print("Failed to fetch portfolio. Check the address and try again.")
        return

    print(f"\n{'=' * 60}")
    print(f"Wallet Portfolio: {portfolio.coldkey[:16]}...")
    print(f"{'=' * 60}")
    print(f"  Free Balance:       {portfolio.free_balance_tao:.6f} TAO (${portfolio.free_balance_usd:.2f})")
    print(f"  Total Staked:       {portfolio.total_staked_tao:.6f} TAO")
    print(f"  Total Alpha Value:  {portfolio.total_alpha_value_tao:.6f} TAO")
    print(f"  Portfolio Total:    {portfolio.total_portfolio_tao:.6f} TAO (${portfolio.total_portfolio_usd:.2f})")
    print(f"  TAO Price:          ${portfolio.tao_price_usd:.2f}")
    print(f"  Timestamp:          {portfolio.timestamp}")

    if portfolio.subnet_stakes:
        print(f"\n  Subnet Stakes ({len(portfolio.subnet_stakes)}):")
        print(f"  {'-' * 100}")
        print(f"  {'NetUID':<8} {'Name':<20} {'Symbol':<10} {'Alpha Held':<14} {'Alpha Price':<14} {'Value (TAO)':<14} {'Value (USD)':<12}")
        print(f"  {'-' * 100}")

        for s in portfolio.subnet_stakes:
            name = str(s.get('subnet_name', ''))[:19]
            print(f"  {s.get('netuid', 0):<8} {name:<20} {s.get('symbol', ''):<10} "
                  f"{s.get('alpha_held', 0):<14.6f} {s.get('alpha_price', 0):<14.8f} "
                  f"{s.get('alpha_value_tao', 0):<14.6f} ${s.get('alpha_value_usd', 0):<11.2f}")

        print(f"  {'-' * 100}")
    else:
        print("\n  No subnet stakes found for this wallet.")


def main():
    parser = argparse.ArgumentParser(description='Fetch Bittensor subnet data')
    parser.add_argument('--netuid', type=int, help='Fetch specific subnet by netuid')
    parser.add_argument('--price', action='store_true', help='Fetch TAO price only')
    parser.add_argument('--wallet', type=str, help='Fetch wallet portfolio (provide SS58 coldkey address)')
    parser.add_argument('--all', action='store_true', help='Fetch all data (subnets + price)')

    args = parser.parse_args()

    try:
        if args.wallet:
            fetch_wallet(args.wallet)
        elif args.price:
            fetch_price()
        elif args.all:
            fetch_price()
            fetch_subnets()
        else:
            fetch_subnets(args.netuid)
    except ImportError as e:
        print(f"\nError: Missing dependency - {e}")
        print("Please install requirements: pip install -r requirements.txt")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)


if __name__ == '__main__':
    main()
