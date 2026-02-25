```python
"""
webhook_server.py

A FastAPI webhook server that accepts simple JSON webhooks and instructs the NautilusBackend
to execute orders. It runs the backend in the same process and same asyncio event loop.

Example webhook JSON (POST /webhook):
{
  "webhook_id": "abc-123",    # optional, use for idempotency externally
  "token": "ETH",
  "side": "buy",
  "qty": 0.01,
  "price": null,
  "order_type": "market"
}

Start with:
    python -m uvicorn webhook_server:app --host 0.0.0.0 --port 8000

This file assumes nautilus_backend.py is in the same folder and that NautilusTrader is
installed and configured (API keys, adapters).
"""

import asyncio
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from nautilus_backend import NautilusBackend
from nautilus_trader.adapters.kraken import KrakenProductType

app = FastAPI(title="Webhook -> NautilusTrader bridge")

# Configure tokens you want the backend to manage.
# The backend will create one ExecTester strategy per token/instrument.
TOKENS = ["ETH", "BTC", "LTC", "XRP", "ADA"]

# Provide per-token default sizes if you like
TOKEN_QTY_MAP = {
    "ETH": Decimal("0.001"),
    "BTC": Decimal("0.0001"),
    "LTC": Decimal("0.01"),
    "XRP": Decimal("10"),
    "ADA": Decimal("20"),
}

# Instantiate backend (shared)
backend = NautilusBackend(tokens=TOKENS, product_type=KrakenProductType.SPOT, token_qty_map=TOKEN_QTY_MAP)


class WebhookPayload(BaseModel):
    webhook_id: Optional[str] = Field(None, description="External id for idempotency")
    token: str
    side: str
    qty: Decimal
    price: Optional[Decimal] = None
    order_type: str = "market"


@app.on_event("startup")
async def startup_event() -> None:
    # Build node and strategies, then start node
    backend.build()
    await backend.start()
    app.state.backend = backend
    app.state.idempotency_cache = set()  # very small in-memory cache for demo only


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await backend.stop()


@app.post("/webhook")
async def webhook(payload: WebhookPayload):
    # Basic authentication / signature verification should be added here for real webhooks.
    # Example lightweight idempotency handling (in-memory; use Redis/DB in production)
    if payload.webhook_id:
        cache = app.state.idempotency_cache
        if payload.webhook_id in cache:
            return {"status": "ignored", "reason": "duplicate webhook_id"}
        cache.add(payload.webhook_id)

    # Validate token
    token = payload.token.upper()
    if token not in TOKENS:
        raise HTTPException(status_code=400, detail=f"token {token} not allowed")

    # Simple side validation
    if payload.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")

    # Forward to backend
    result = await backend.submit_order(
        token=token,
        side=payload.side,
        qty=payload.qty,
        price=payload.price,
        order_type=payload.order_type,
    )

    if result.get("status") == "accepted":
        return {"status": "accepted", "instrument": result.get("instrument"), "client_order_id": result.get("client_order_id")}
    else:
        # Map backend errors to HTTP errors
        raise HTTPException(status_code=500, detail=result.get("reason", "unknown error"))