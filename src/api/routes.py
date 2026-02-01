"""
API Routes

Flask routes for the Bittensor data API.
These endpoints are designed to be easily consumed by Google Sheets.
"""

import logging
from flask import Blueprint, jsonify, request, Response
import csv
import io

from ..services.bittensor_service import get_bittensor_service
from ..services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

api = Blueprint('api', __name__, url_prefix='/api/v1')


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@api.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'service': 'bittensor-tracker'
    })



# ---------------------------------------------------------------------------
# Subnets
# ---------------------------------------------------------------------------

@api.route('/subnets', methods=['GET'])
def get_all_subnets():
    """
    Get all subnet information.

    Query params:
        format: 'json' (default) or 'csv'
        use_cache: 'true' (default) or 'false'
    """
    output_format = request.args.get('format', 'json').lower()
    use_cache = request.args.get('use_cache', 'true').lower() == 'true'

    bt_service = get_bittensor_service()
    subnets = bt_service.get_all_subnets(use_cache=use_cache)

    if not subnets:
        if output_format == 'csv':
            return Response("status\nLoading subnet data - please retry in a few minutes\n", mimetype='text/csv')
        return jsonify({
            'status': 'loading',
            'message': 'Subnet data is being fetched in the background. Please retry in a few minutes.',
            'count': 0,
            'subnets': []
        })

    subnet_dicts = bt_service.to_dict_list(subnets)

    if output_format == 'csv':
        return _to_csv_response(subnet_dicts)

    return jsonify({
        'count': len(subnets),
        'subnets': subnet_dicts
    })


@api.route('/subnets/<int:netuid>', methods=['GET'])
def get_subnet(netuid: int):
    """Get information for a specific subnet."""
    bt_service = get_bittensor_service()
    subnet = bt_service.get_subnet(netuid)

    if not subnet:
        return jsonify({'error': f'Subnet {netuid} not found'}), 404

    return jsonify(bt_service.to_dict_list([subnet])[0])


@api.route('/subnets/emissions', methods=['GET'])
def get_subnet_emissions():
    """
    Get emission data for all subnets.

    Query params:
        format: 'json' (default) or 'csv'
    """
    output_format = request.args.get('format', 'json').lower()

    bt_service = get_bittensor_service()
    subnets = bt_service.get_all_subnets()

    if not subnets:
        if output_format == 'csv':
            return Response("netuid,emission_percentage\n", mimetype='text/csv')
        return jsonify({'status': 'loading', 'emissions': []})

    emissions = [
        {
            'netuid': s.netuid,
            'name': s.name,
            'emission_percentage': s.emission_percentage
        }
        for s in sorted(subnets, key=lambda x: x.netuid)
    ]

    if output_format == 'csv':
        return _to_csv_response(emissions)

    return jsonify({
        'count': len(emissions),
        'emissions': emissions
    })


# ---------------------------------------------------------------------------
# Google Sheets Optimized Endpoints
# ---------------------------------------------------------------------------

@api.route('/sheets/subnets', methods=['GET'])
def sheets_subnets():
    """
    Google Sheets optimized CSV for all subnets.

    Usage in Google Sheets:
        =IMPORTDATA("http://your-server:5000/api/v1/sheets/subnets")
    """
    bt_service = get_bittensor_service()
    subnets = bt_service.get_all_subnets()

    if not subnets:
        return Response(
            "netuid,name,symbol,emission_pct,alpha_price\n",
            mimetype='text/csv'
        )

    data = [
        {
            'netuid': s.netuid,
            'name': s.name,
            'symbol': s.symbol,
            'emission_pct': s.emission_percentage,
            'alpha_price': s.alpha_price,
        }
        for s in sorted(subnets, key=lambda x: x.netuid)
    ]

    return _to_csv_response(data)



@api.route('/sheets/portfolio', methods=['GET'])
def sheets_portfolio():
    """
    Google Sheets optimized CSV for wallet portfolio summary.

    Query params:
        address: SS58 coldkey address (required)

    Usage in Google Sheets:
        =IMPORTDATA("http://your-server:5000/api/v1/sheets/portfolio?address=5Cai...")
    """
    address = request.args.get('address', '').strip()
    if not address:
        return Response("error\nMissing 'address' query parameter\n", mimetype='text/csv')

    wallet_service = get_wallet_service()
    portfolio = wallet_service.get_portfolio(address)

    if not portfolio:
        return Response("error\nFailed to fetch portfolio\n", mimetype='text/csv')

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'coldkey', 'free_balance_tao',
        'total_staked_tao',
        'total_portfolio_tao',
        'timestamp'
    ])
    writer.writerow([
        portfolio.coldkey,
        portfolio.free_balance_tao,
        portfolio.total_staked_tao,
        portfolio.total_portfolio_tao,
        portfolio.timestamp
    ])

    return Response(output.getvalue(), mimetype='text/csv')


@api.route('/sheets/stakes', methods=['GET'])
def sheets_stakes():
    """
    Google Sheets optimized CSV for per-subnet stake breakdown.

    Query params:
        address: SS58 coldkey address (required)

    Usage in Google Sheets:
        =IMPORTDATA("http://your-server:5000/api/v1/sheets/stakes?address=5Cai...")
    """
    address = request.args.get('address', '').strip()
    if not address:
        return Response("error\nMissing 'address' query parameter\n", mimetype='text/csv')

    wallet_service = get_wallet_service()
    portfolio = wallet_service.get_portfolio(address)

    if not portfolio:
        return Response("error\nFailed to fetch portfolio\n", mimetype='text/csv')

    if not portfolio.subnet_stakes:
        return Response(
            "netuid,subnet_name,symbol,hotkey,alpha_held,alpha_value_tao\n",
            mimetype='text/csv'
        )

    return _to_csv_response(portfolio.subnet_stakes)


# ---------------------------------------------------------------------------
# Wallet JSON Endpoints
# ---------------------------------------------------------------------------

@api.route('/wallet/<address>/portfolio', methods=['GET'])
def get_wallet_portfolio(address: str):
    """
    Get full wallet portfolio.

    Args:
        address: SS58 coldkey address
    """
    wallet_service = get_wallet_service()
    portfolio = wallet_service.get_portfolio(address)

    if not portfolio:
        return jsonify({'error': f'Failed to fetch portfolio for {address}'}), 500

    return jsonify(wallet_service.to_dict(portfolio))


@api.route('/wallet/<address>/stakes', methods=['GET'])
def get_wallet_stakes(address: str):
    """
    Get per-subnet stake breakdown for a wallet.

    Args:
        address: SS58 coldkey address
    """
    wallet_service = get_wallet_service()
    portfolio = wallet_service.get_portfolio(address)

    if not portfolio:
        return jsonify({'error': f'Failed to fetch stakes for {address}'}), 500

    return jsonify({
        'coldkey': portfolio.coldkey,
        'count': len(portfolio.subnet_stakes),
        'stakes': portfolio.subnet_stakes,
        'timestamp': portfolio.timestamp
    })


# ---------------------------------------------------------------------------
# Wallet Transfers & Delegations
# ---------------------------------------------------------------------------

@api.route('/wallet/<address>/transfers', methods=['GET'])
def get_wallet_transfers(address: str):
    """Get recent TAO transfers for a wallet."""
    limit = request.args.get('limit', 50, type=int)
    wallet_service = get_wallet_service()
    transfers = wallet_service.get_transfers(address, limit=limit)
    return jsonify({'coldkey': address, 'count': len(transfers), 'transfers': transfers})


@api.route('/wallet/<address>/delegations', methods=['GET'])
def get_wallet_delegations(address: str):
    """Get recent delegation (stake/unstake) events for a wallet."""
    limit = request.args.get('limit', 50, type=int)
    wallet_service = get_wallet_service()
    delegations = wallet_service.get_delegations(address, limit=limit)
    return jsonify({'coldkey': address, 'count': len(delegations), 'delegations': delegations})


@api.route('/sheets/transfers', methods=['GET'])
def sheets_transfers():
    """Google Sheets CSV for wallet transfers."""
    address = request.args.get('address', '').strip()
    if not address:
        return Response("error\nMissing 'address' query parameter\n", mimetype='text/csv')
    limit = request.args.get('limit', 50, type=int)
    wallet_service = get_wallet_service()
    transfers = wallet_service.get_transfers(address, limit=limit)
    if not transfers:
        return Response("block,timestamp,direction,from,to,amount_tao,fee_tao,extrinsic_id\n", mimetype='text/csv')
    return _to_csv_response(transfers)


@api.route('/sheets/delegations', methods=['GET'])
def sheets_delegations():
    """Google Sheets CSV for wallet delegation events."""
    address = request.args.get('address', '').strip()
    if not address:
        return Response("error\nMissing 'address' query parameter\n", mimetype='text/csv')
    limit = request.args.get('limit', 50, type=int)
    wallet_service = get_wallet_service()
    delegations = wallet_service.get_delegations(address, limit=limit)
    if not delegations:
        return Response("block,timestamp,action,netuid,subnet_name,symbol,delegate_name,delegate,amount_tao,alpha,alpha_price_tao,extrinsic_id\n", mimetype='text/csv')
    return _to_csv_response(delegations)


@api.route('/sheets/whales', methods=['GET'])
def sheets_whales():
    """Google Sheets CSV for top whale delegation transactions."""
    limit = request.args.get('limit', 10, type=int)
    wallet_service = get_wallet_service()
    rows = wallet_service.get_whale_transactions(limit_per_whale=limit)
    if not rows:
        return Response("whale,timestamp,action,netuid,subnet_name,symbol,delegate_name,amount_tao,alpha,alpha_price_tao\n", mimetype='text/csv')
    return _to_csv_response(rows)


# ---------------------------------------------------------------------------
# Block Info
# ---------------------------------------------------------------------------

@api.route('/block', methods=['GET'])
def get_current_block():
    """Get current block number from the Bittensor network."""
    bt_service = get_bittensor_service()
    block = bt_service.get_current_block()

    return jsonify({
        'block': block,
        'network': bt_service.network
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_csv_response(data: list[dict]) -> Response:
    """Convert list of dicts to CSV response."""
    if not data:
        return Response("", mimetype='text/csv')

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)

    return Response(output.getvalue(), mimetype='text/csv')
