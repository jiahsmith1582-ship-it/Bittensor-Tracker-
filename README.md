# Bittensor Subnet Tracker

A Python API service that fetches live subnet prices and data from the Bittensor blockchain, designed for integration with Google Sheets.

## Features

- Live subnet data from Bittensor blockchain
- TAO price from CoinGecko
- REST API with JSON and CSV output formats
- Google Sheets compatible endpoints (IMPORTDATA ready)
- Built-in caching to reduce blockchain queries

## Quick Start

### 1. Install Dependencies

```bash
cd bittensor-tracker
pip install -r requirements.txt
```

### 2. Test Connection

```bash
# Test fetching subnet data
python fetch_subnets.py

# Fetch TAO price
python fetch_subnets.py --price

# Fetch specific subnet
python fetch_subnets.py --netuid 1
```

### 3. Start the API Server

```bash
# Development mode
python run.py

# Production mode (with gunicorn)
gunicorn run:app -b 0.0.0.0:5000
```

## API Endpoints

| Endpoint | Description | Format |
|----------|-------------|--------|
| `/api/v1/health` | Health check | JSON |
| `/api/v1/tao/price` | Current TAO price | JSON |
| `/api/v1/subnets` | All subnet data | JSON/CSV |
| `/api/v1/subnets/<netuid>` | Specific subnet | JSON |
| `/api/v1/subnets/emissions` | Emission percentages | JSON/CSV |
| `/api/v1/sheets/subnets` | Google Sheets optimized | CSV |
| `/api/v1/sheets/price` | Google Sheets price | CSV |
| `/api/v1/block` | Current block number | JSON |

### Query Parameters

- `format=csv` - Return CSV instead of JSON
- `use_cache=false` - Force fresh data fetch

## Google Sheets Integration

### Using IMPORTDATA

In your Google Sheet, use:

```
=IMPORTDATA("http://your-server:5000/api/v1/sheets/subnets")
```

For TAO price:
```
=IMPORTDATA("http://your-server:5000/api/v1/sheets/price")
```

### Auto-Refresh

Google Sheets IMPORTDATA refreshes approximately every hour. For more frequent updates, consider using Google Apps Script with time-based triggers.

## Project Structure

```
bittensor-tracker/
├── run.py                 # Main entry point
├── fetch_subnets.py       # CLI testing tool
├── requirements.txt       # Python dependencies
├── .env.example           # Environment template
├── .gitignore
└── src/
    ├── config.py          # Configuration
    ├── api/
    │   ├── app.py         # Flask app factory
    │   └── routes.py      # API endpoints
    └── services/
        ├── bittensor_service.py  # Blockchain access
        └── price_service.py      # Price fetching
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 5000 | API server port |
| `BITTENSOR_NETWORK` | finney | Network (finney/test) |
| `LOG_LEVEL` | INFO | Logging level |
| `SUBNET_CACHE_TTL` | 60 | Subnet cache (seconds) |
| `PRICE_CACHE_TTL` | 30 | Price cache (seconds) |

## Deployment Options

### Local Development
```bash
python run.py
```

### Production (Linux)
```bash
gunicorn run:app -b 0.0.0.0:5000 --workers 4
```

### Docker (coming soon)
A Dockerfile will be added for containerized deployment.

### Cloud Deployment
For Google Sheets access, deploy to a cloud provider:
- Google Cloud Run
- AWS Lambda + API Gateway
- Heroku
- Railway

## Troubleshooting

### "bittensor not installed"
```bash
pip install bittensor
```

### Connection timeout
The Bittensor network can be slow. The first query may take 30-60 seconds. Subsequent queries use cache.

### Rate limiting
CoinGecko has rate limits on their free API. If you hit limits, wait a minute before retrying.
