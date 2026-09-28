from __future__ import annotations
import base64, json, time, uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode
import requests

class RobinhoodAPIError(RuntimeError): pass

def _encode_params(params: dict[str, Any] | None) -> str:
    if not params: return ""
    pairs = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    return "?" + urlencode(pairs)

def floor_step(value: float, step: float) -> float:
    if step <= 0: return value
    v, s = Decimal(str(value)), Decimal(str(step))
    return float((v / s).to_integral_value(rounding=ROUND_DOWN) * s)

class RobinhoodCrypto:
    BASE_URL = "https://trading.robinhood.com"

    def __init__(self, api_key: str, private_key_b64: str, timeout: float = 10.0) -> None:
        from nacl.signing import SigningKey
        self.api_key = api_key
        self.private_key = SigningKey(base64.b64decode(private_key_b64))
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, timestamp: int, path: str, method: str, body: str) -> dict[str, str]:
        signed = self.private_key.sign(f"{self.api_key}{timestamp}{path}{method}{body}".encode())
        return {"x-api-key": self.api_key, "x-signature": base64.b64encode(signed.signature).decode(),
                "x-timestamp": str(timestamp), "Content-Type": "application/json; charset=utf-8"}

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, retries: int = 3) -> Any:
        body = "" if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        last = None
        for attempt in range(retries):
            try:
                response = self.session.request(method.upper(), self.BASE_URL + path, headers=self._headers(int(time.time()), path, method.upper(), body), data=body or None, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(0.5 * 2**attempt); continue
                if response.status_code >= 400:
                    raise RobinhoodAPIError(f"{response.status_code}: {response.text[:1000]}")
                return response.json() if response.content else {}
            except (requests.RequestException, ValueError, RobinhoodAPIError) as exc:
                last = exc
                if isinstance(exc, RobinhoodAPIError) and not str(exc).startswith(("429","5")): raise
                time.sleep(0.5 * 2**attempt)
        raise RobinhoodAPIError(f"Request failed after retries: {last}")

    def accounts(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v2/crypto/trading/accounts/")
        return data.get("results", data if isinstance(data, list) else [])

    def account(self, account_number: str) -> dict[str, Any]:
        for account in self.accounts():
            if account.get("account_number") == account_number: return account
        raise RobinhoodAPIError(f"Crypto account not found: {account_number}")

    def trading_pairs(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        path = "/api/v2/crypto/trading/trading_pairs/" + _encode_params({"symbol": symbols} if symbols else None)
        results = []
        while path:
            data = self.request("GET", path); results.extend(data.get("results", []))
            nxt = data.get("next"); path = nxt.replace(self.BASE_URL, "") if nxt else ""
        return results

    def holdings(self, account_number: str) -> list[dict[str, Any]]:
        path = "/api/v2/crypto/trading/holdings/" + _encode_params({"account_number": account_number})
        results = []
        while path:
            data = self.request("GET", path); results.extend(data.get("results", []))
            nxt = data.get("next"); path = nxt.replace(self.BASE_URL, "") if nxt else ""
        return results

    def best_bid_ask(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        data = self.request("GET", "/api/v2/crypto/marketdata/best_bid_ask/" + _encode_params({"symbol": symbols}))
        return {r["symbol"]: {"bid": float(r["bid"]), "ask": float(r["ask"])} for r in data.get("results", []) if r.get("bid") is not None and r.get("ask") is not None}

    def estimated_price(self, symbol: str, side: str, quantity: float) -> dict[str, Any]:
        data = self.request("GET", "/api/v2/crypto/trading/estimated_price/" + _encode_params({"symbol": symbol, "side": side, "quantity": str(quantity)}))
        rows = data.get("results", [])
        if not rows: raise RobinhoodAPIError(f"No estimated price for {symbol}")
        return rows[0]

    def place_order(self, *, account_number: str, symbol: str, side: str, order_type: str, order_config: dict[str, str]) -> dict[str, Any]:
        body = {"symbol": symbol, "client_order_id": str(uuid.uuid4()), "side": side, "type": order_type,
                f"{order_type}_order_config": order_config}
        return self.request("POST", "/api/v2/crypto/trading/orders/" + _encode_params({"account_number": account_number}), body)

    def place_market_order(self, *, account_number: str, symbol: str, side: str, asset_quantity: float) -> dict[str, Any]:
        return self.place_order(account_number=account_number,symbol=symbol,side=side,order_type="market",
                                order_config={"asset_quantity":str(asset_quantity)})

    def place_market_buy_quote(self, *, account_number: str, symbol: str, quote_amount: float) -> dict[str, Any]:
        return self.place_order(account_number=account_number,symbol=symbol,side="buy",order_type="market",
                                order_config={"quote_amount":str(quote_amount)})

    def place_stop_loss_sell(self, *, account_number: str, symbol: str, asset_quantity: float, stop_price: float) -> dict[str, Any]:
        return self.place_order(account_number=account_number,symbol=symbol,side="sell",order_type="stop_loss",
                                order_config={"asset_quantity":str(asset_quantity),"stop_price":str(stop_price),"time_in_force":"gtc"})

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/v2/crypto/trading/orders/{order_id}/cancel/")

    def order(self, account_number: str, order_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v2/crypto/trading/orders/{order_id}/" + _encode_params({"account_number": account_number}))

    def wait_for_fill(self, account_number: str, order_id: str, timeout_seconds: float = 15.0) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last = {}
        while time.time() < deadline:
            last = self.order(account_number, order_id)
            if str(last.get("state","")).lower() in {"filled","failed","canceled"}: return last
            time.sleep(1)
        return last
