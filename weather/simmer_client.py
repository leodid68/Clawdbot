"""Simmer API client — class-based, with retry/backoff and structured logging."""

import json
import logging
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import TRADE_SOURCE

logger = logging.getLogger(__name__)

SIMMER_API_BASE = "https://api.simmer.markets"
_RETRYABLE_CODES = {429, 500, 502, 503, 504}


class SimmerClient:
    """Encapsulates all Simmer API interactions.

    Accepts the API key at construction for testability (dependency injection).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = SIMMER_API_BASE,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max_retries
        self.base_delay = base_delay

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    def _request(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        """Make authenticated request with retry on transient errors."""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(self.max_retries + 1):
            try:
                if method == "GET":
                    req = Request(url, headers=headers)
                else:
                    body = json.dumps(data).encode() if data else None
                    req = Request(url, data=body, headers=headers, method=method)

                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode())

            except HTTPError as exc:
                if exc.code in _RETRYABLE_CODES and attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(
                        "Simmer HTTP %d on %s %s — retry %d/%d in %.1fs",
                        exc.code, method, endpoint, attempt + 1, self.max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                error_body = exc.read().decode() if exc.fp else str(exc)
                logger.error("Simmer HTTP %d: %s %s — %s", exc.code, method, endpoint, error_body)
                return {"error": f"HTTP {exc.code}: {error_body}"}

            except URLError as exc:
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(
                        "Simmer URL error on %s %s — retry %d/%d in %.1fs",
                        method, endpoint, attempt + 1, self.max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("Simmer URL error: %s %s — %s", method, endpoint, exc.reason)
                return {"error": str(exc.reason)}

            except TimeoutError:
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(
                        "Simmer timeout on %s %s — retry %d/%d in %.1fs",
                        method, endpoint, attempt + 1, self.max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("Simmer timeout: %s %s", method, endpoint)
                return {"error": "timeout"}

        return {"error": "max retries exceeded"}

    # ------------------------------------------------------------------
    # Portfolio & Context
    # ------------------------------------------------------------------

    def get_portfolio(self) -> dict | None:
        result = self._request("GET", "/api/sdk/portfolio")
        if "error" in result:
            logger.warning("Portfolio fetch failed: %s", result["error"])
            return None
        return result

    def get_market_context(self, market_id: str, my_probability: float | None = None) -> dict | None:
        endpoint = f"/api/sdk/context/{market_id}"
        if my_probability is not None:
            endpoint += f"?my_probability={my_probability}"
        result = self._request("GET", endpoint)
        if "error" in result:
            return None
        return result

    def get_price_history(self, market_id: str) -> list:
        result = self._request("GET", f"/api/sdk/markets/{market_id}/history")
        if "error" in result:
            return []
        return result.get("points", [])

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def fetch_weather_markets(self) -> list:
        """Fetch active weather markets. Uses authenticated request."""
        result = self._request("GET", "/api/markets?tags=weather&status=active&limit=100")
        if "error" in result:
            logger.error("Failed to fetch weather markets: %s", result["error"])
            return []
        return result.get("markets", [])

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> list:
        result = self._request("GET", "/api/sdk/positions")
        if "error" in result:
            logger.warning("Error fetching positions: %s", result["error"])
            return []
        return result.get("positions", [])

    def get_position(self, market_id: str) -> dict | None:
        """Fetch a single fresh position (for race-condition guard)."""
        positions = self.get_positions()
        for pos in positions:
            if pos.get("market_id") == market_id:
                return pos
        return None

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    def execute_trade(self, market_id: str, side: str, amount: float) -> dict:
        return self._request("POST", "/api/sdk/trade", {
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "venue": "polymarket",
            "source": TRADE_SOURCE,
        })

    def execute_sell(self, market_id: str, shares: float) -> dict:
        return self._request("POST", "/api/sdk/trade", {
            "market_id": market_id,
            "side": "yes",
            "action": "sell",
            "shares": shares,
            "venue": "polymarket",
            "source": TRADE_SOURCE,
        })

    # ------------------------------------------------------------------
    # Risk Monitoring
    # ------------------------------------------------------------------

    def set_risk_monitor(
        self, market_id: str, side: str,
        stop_loss_pct: float = 0.20, take_profit_pct: float = 0.50,
    ) -> dict | None:
        result = self._request("POST", f"/api/sdk/positions/{market_id}/monitor", {
            "side": side,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
        })
        if "error" in result:
            logger.warning("Risk monitor failed: %s", result["error"])
            return None
        return result

    def get_risk_monitors(self) -> dict | None:
        result = self._request("GET", "/api/sdk/positions/monitors")
        if "error" in result:
            return None
        return result

    def remove_risk_monitor(self, market_id: str, side: str) -> dict:
        return self._request("DELETE", f"/api/sdk/positions/{market_id}/monitor?side={side}")


def get_api_key() -> str:
    """Get Simmer API key from environment, exit if missing."""
    key = os.environ.get("SIMMER_API_KEY")
    if not key:
        logger.error("SIMMER_API_KEY environment variable not set")
        print("Error: SIMMER_API_KEY environment variable not set")
        print("Get your API key from: simmer.markets/dashboard -> SDK tab")
        sys.exit(1)
    return key
