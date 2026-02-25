from krakenHandler import KrakenWS
from loggingHandler import tradesLog, logger
from webhookHandler import parse_token
from investorManager import investor_manager
from utils import (
    is_open_order,
    tail_log_file,
    handle_balance_update,
    handle_trade_update,
)
from telegramHandler import send_telegram
from quart import Quart, render_template, websocket, request
import json
import asyncio
from dotenv import load_dotenv
import os
import websockets
import pandas as pd
import time
from collections import deque
from contextlib import suppress


load_dotenv()

API_KEY = str(os.getenv("KRAKEN_KEY"))
API_SECRET = str(os.getenv("KRAKEN_SECRET"))

#convert the token list from a string into a list of tokens
tokenList = [t.strip() for t in os.getenv("TOKEN_LIST", "").split(",") if t.strip()]

app = Quart(__name__, static_folder="static", template_folder="templates")

kraken = KrakenWS(api_key=API_KEY, api_secret=API_SECRET)


balances = {}
open_trades = {}    # Stores currently open trades (trade_id -> trade info)
# we use live ticker prices from KrakenWS.ticker_prices instead of OHLC cache

# Recent statistics tracking
recent_stats = {
    "reset_timestamp": time.time(),
    "portfolio_value_at_reset": kraken.calculate_portfolio_value() if 'kraken' in globals() else 0.0,
    "total_fees": 0.0,
    "wins_count": 0,
    "losses_count": 0
}

connected_clients = set()


# Create wrapper functions that include the state
async def _handle_balance_update(msg):
    await handle_balance_update(msg, balances, broadcast_to_clients)

async def _handle_trade_update(msg):
    await handle_trade_update_with_stats(msg, open_trades, broadcast_to_clients)

def _tail_log_file(path, channel_name):
    return tail_log_file(path, channel_name, broadcast_to_clients)

async def handle_trade_update_with_stats(msg, open_trades, broadcast_func):
    """Handle trade updates and track statistics."""
    for report in msg.get("data", []):
        order_id = report.get("order_id") or report.get("id")
        if not order_id:
            continue

        # Track fees and wins/losses for filled orders
        if report.get("order_status") in {"filled", "done", "closed"}:
            # Extract fee from execution data
            fee_usd = float(report.get("fee_usd_equiv", 0.0))
            if fee_usd > 0:
                recent_stats["total_fees"] += fee_usd

            # For sell orders, check if it was a win or loss
            if report.get("side") == "sell":
                # Calculate realized PnL for this trade
                cost = float(report.get("cost", 0))
                fee = float(report.get("fee", 0))
                # For sell orders, cost is positive (money received), fee is subtracted
                realized_pnl = cost - fee

                if realized_pnl > 0:
                    recent_stats["wins_count"] += 1
                    tradesLog.info(f"Trade WIN: +${realized_pnl:.2f} realized PnL")
                elif realized_pnl < 0:
                    recent_stats["losses_count"] += 1
                    tradesLog.info(f"Trade LOSS: ${realized_pnl:.2f} realized PnL")
                # Zero PnL trades are not counted as wins or losses

        # Log execution details for monitoring
        if report.get("status") in {"filled", "done", "closed"}:
            symbol = report.get("symbol", "UNKNOWN")
            side = report.get("side", "UNKNOWN")
            filled_qty = float(report.get("last_qty", 0) or report.get("filled_qty", 0))
            fill_price = float(report.get("last_price", 0) or report.get("price", 0))
            cost = float(report.get("cost", 0))
            status = report.get("order_status", "UNKNOWN")

            if status == 'filled':
                tradesLog.info(f"Order {order_id} {side} {filled_qty} {symbol} filled at {fill_price}")

        if is_open_order(report):
            open_trades[order_id] = report
            tradesLog.info(f"New open order {order_id}: {report.get('symbol')} {report.get('side')} {report.get('order_qty')}")
        else:
            # Remove closed/canceled/filled orders from the open set
            if order_id in open_trades:
                open_trades.pop(order_id, None)

    # Only broadcast still-open items
    await broadcast_func({"channel": "executions", "data": list(open_trades.values())})

    # Broadcast updated statistics
    await broadcast_recent_stats()

    # Recalculate and broadcast share price after trades
    await broadcast_share_price_update()

async def calculate_recent_stats():
    """Calculate current recent statistics."""
    current_value = kraken.calculate_portfolio_value()
    value_at_reset = recent_stats["portfolio_value_at_reset"]

    pnl_dollar = current_value - value_at_reset
    pnl_percent = (pnl_dollar / value_at_reset * 100) if value_at_reset > 0 else 0.0
    fee_percent = (recent_stats["total_fees"] / current_value * 100) if current_value > 0 else 0.0

    return {
        "wins": recent_stats["wins_count"],
        "losses": recent_stats["losses_count"],
        "pnl_dollar": pnl_dollar,
        "pnl_percent": pnl_percent,
        "fee_dollar": recent_stats["total_fees"],
        "fee_percent": fee_percent
    }

async def broadcast_recent_stats():
    """Broadcast current recent statistics to all clients."""
    try:
        stats = await calculate_recent_stats()
        await broadcast_to_clients({
            "channel": "recent_stats",
            "data": stats
        })
    except Exception as e:
        logger.exception("Error broadcasting recent stats")

async def broadcast_investor_data():
    """Broadcast current investor data to all clients."""
    try:
        investors = investor_manager.get_investor_breakdown()
        await broadcast_to_clients({
            "channel": "investors",
            "data": investors
        })
        await broadcast_to_clients({
            "channel": "fund_metadata",
            "data": {
                "share_price": investor_manager.get_current_share_price(),
                "total_shares": investor_manager.get_total_shares()
            }
        })
    except Exception as e:
        logger.exception("Error broadcasting investor data")

async def broadcast_share_price_update():
    """Broadcast updated share price after trades."""
    try:
        current_value = kraken.calculate_portfolio_value()
        new_share_price = investor_manager.calculate_share_price(current_value)

        await broadcast_to_clients({
            "channel": "portfolio",
            "data": {
                "timestamp": int(time.time()),
                "share_price": new_share_price,
                "history": list(kraken.portfolio_values)  # Keep history for chart
            }
        })

        # Also broadcast updated fund metadata
        await broadcast_to_clients({
            "channel": "fund_metadata",
            "data": {
                "share_price": new_share_price,
                "total_shares": investor_manager.get_total_shares()
            }
        })
    except Exception as e:
        logger.exception("Error broadcasting share price update")

# Set Kraken callbacks with our wrapped versions
kraken.on_balance_update = _handle_balance_update
kraken.on_trade_update = _handle_trade_update
# no OHLC callback; we rely on ticker channel for live prices

background_tasks = []

async def broadcast_portfolio_value():
    """Background task to broadcast portfolio value updates."""
    while True:
        try:
            if connected_clients:  # Only broadcast if clients are connected
                value = kraken.calculate_portfolio_value()
                await broadcast_to_clients({
                    "channel": "portfolio",
                    "data": {
                        "timestamp": int(time.time()),
                        "value": value,
                        "history": list(kraken.portfolio_values)  # Send full history on connect
                    }
                })
        except Exception as e:
            logger.exception("Error broadcasting portfolio value")
        await asyncio.sleep(60)  # Update every minute

async def initialize_kraken():
    """Initialize Kraken connection without blocking startup."""
    try:
        # Start connection tasks
        asyncio.create_task(kraken.connect())
        return True
    except Exception as e:
        logger.exception("Error initializing Kraken connection")
        return False

@app.before_serving
async def startup():
    """Startup handler that runs quickly for ASGI lifespan protocol."""
    try:
        # Initialize investor manager with current portfolio value
        initial_aum = kraken.calculate_portfolio_value()
        investor_manager.initialize_fund(initial_aum)

        # Schedule initial tasks
        t1 = asyncio.create_task(kraken.auto_refresh_token(tokenList))
        t2 = asyncio.create_task(initialize_kraken())
        t3 = asyncio.create_task(_tail_log_file("trades-log", "trades_log"))
        t4 = asyncio.create_task(_tail_log_file("bot-log", "system_log"))
        t5 = asyncio.create_task(broadcast_portfolio_value())

        # Track all background tasks
        background_tasks.extend([t1, t2, t3, t4, t5])

        logger.info("Background tasks scheduled")
    except Exception as e:
        logger.exception("Error during startup")

@app.after_serving
async def shutdown():
    """Graceful shutdown handler."""
    logger.info("Starting graceful shutdown...")
    
    try:
        # First close Kraken connection
        logger.info("Closing Kraken connection...")
        with suppress(Exception):
            await asyncio.wait_for(kraken.close(), timeout=5.0)
        
        # Then cancel all background tasks
        logger.info("Cancelling background tasks...")
        for t in background_tasks:
            t.cancel()
        
        if background_tasks:
            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(*background_tasks, return_exceptions=True),
                    timeout=5.0
                )
        
        logger.info("Shutdown complete")
    except Exception as e:
        logger.exception("Error during shutdown")

@app.route('/')
async def index():
    return await render_template("index.html")

@app.route('/user', methods=['POST'])
async def user():
    try:
        data = await request.get_json()
        action = data.get("action")
        if action == "exit_all":
            await kraken.exitAll()
            logger.info("All positions closed via user action")
        elif action == "reset_stats":
            # Reset recent statistics
            recent_stats["reset_timestamp"] = time.time()
            recent_stats["portfolio_value_at_reset"] = kraken.calculate_portfolio_value()
            recent_stats["total_fees"] = 0.0
            recent_stats["wins_count"] = 0
            recent_stats["losses_count"] = 0
            logger.info("Recent statistics reset")
            # Broadcast updated stats immediately
            await broadcast_recent_stats()
        elif action == "add_deposit":
            investor_name = data.get("investor_name", "").strip()
            amount = float(data.get("amount", 0))

            if not investor_name or amount <= 0:
                return {"status": "error", "message": "Invalid investor name or amount"}

            try:
                result = investor_manager.add_deposit(investor_name, amount)
                # Broadcast updated investor data
                await broadcast_investor_data()
                return {"status": "ok", "data": result}
            except Exception as e:
                logger.exception(f"Deposit error: {e}")
                return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"User input failure: {e}")

    return {"status": "ok"}

@app.route('/webhook', methods=['POST'])
async def receive_webhook():
    # Minimal webhook handler: support entry (TOKEN:SCORE:DIRECTION) and exit (TOKEN:EXIT)
    # Keep logging light and return structured JSON.
    data = await request.get_data()
    # request.get_data() may return bytes or a string depending on the framework; handle both
    send_telegram("Webhook received")
    
    if isinstance(data, (bytes, bytearray)):
        payload_str = data.decode("utf-8", errors="replace").strip()
    else:
        payload_str = str(data).strip()
    try:
        parsed = parse_token(payload_str)
    except Exception as e:
        logger.warning(f"Invalid webhook payload: {e}")
        return {"status": "error", "reason": "invalid_payload", "message": str(e)}

    token = parsed.get("token")

    # Exit webhook: close the position for this token using available balance
    if parsed.get("exit"):
        if not token:
            return {"status": "error", "reason": "missing_token"}
            
        logger.info(f"Exit webhook received for {token}")
        # Extract base asset from trading pair (e.g., "PEPE" from "PEPE/USD")
        asset = token.split('/')[0] if '/' in token else token
        # Try to determine the current held amount for this asset from balances
        try:
            position_size = float(balances.get(asset, 0.0))
            logger.info(f"Found balance for {asset}: {position_size}, balances={balances}")
        except Exception as e:
            position_size = 0.0
            logger.warning(f"Error getting balance for {asset}, balances={balances}: {e}")

        if position_size <= 0:
            logger.warning(f"No position balance for {asset} to exit (size={position_size})")
            return {"status": "error", "reason": "no_position", "token": token, "asset": asset}

        try:
            await kraken.exitPosition(position_size, token)
            logger.info(f"Exit order submitted for {position_size:.8f} {token}")
            return {"status": "ok", "action": "exit_submitted", "token": token, "size": position_size}
        except Exception as e:
            logger.exception(f"Failed to submit exit for {token}: {e}")
            return {"status": "error", "reason": "exit_failed", "message": str(e)}

    # Entry webhook: only act on BUY signals that passed the score threshold
    if parsed.get("enter") and parsed.get("direction") == "BUY":
        live_price = getattr(kraken, "ticker_prices", {}).get(token, {}).get('ask')
        if live_price is None:
            logger.warning(f"No live ask price for {token}")
            return {"status": "error", "reason": "no_price_for_token"}
        try:
            price = float(live_price)
        except Exception:
            logger.warning(f"Invalid live price for {token}: {live_price}")
            return {"status": "error", "reason": "invalid_price"}

        if price <= 0:
            logger.warning(f"Non-positive price for {token}: {price}")
            return {"status": "error", "reason": "non_positive_price"}

        pct = float(os.getenv("TRADE_PERCENT", "0.01"))
        portfolio_value = kraken.calculate_portfolio_value()
        trade_usd = portfolio_value * pct
        min_trade_usd = float(os.getenv("MIN_TRADE_USD", "5.0"))
        if trade_usd < min_trade_usd:
            logger.info(f"Trade USD ${trade_usd:.2f} below minimum; skipping")
            return {"status": "skipped", "reason": "below_min_trade_usd", "trade_usd": trade_usd}

        position_size = trade_usd / price if price > 0 else 0.0
        try:
            await kraken.enterPosition(position_size, token)
            logger.info(f"Entered {position_size:.8f} {token} (~${trade_usd:.2f} at {price})")
            return {"status": "ok", "action": "entered", "token": token, "size": position_size}
        except Exception as e:
            logger.exception(f"Failed to enter position for {token}: {e}")
            return {"status": "error", "reason": "enter_failed", "message": str(e)}

    logger.info(f"Webhook ignored for {token}: not an entry BUY signal")
    return {"status": "skipped", "reason": "non_entry_or_sell"}


@app.websocket("/data")
async def ws_endpoint():
    # Register this client connection
    # websocket is a context-local proxy; access the underlying object. Suppress type-checker warning.
    ws = websocket._get_current_object()  # type: ignore[attr-defined]
    connected_clients.add(ws)
    
    # Send initial snapshots: balances, open trades, OHLC cache, and recent logs
    try:
        # Balances snapshot (as array)
        if balances:
            balances_arr = [{"asset": a, "balance": b} for a, b in balances.items()]
            await ws.send(json.dumps({"channel": "balances", "data": balances_arr}))
        # Open trades snapshot
        if open_trades:
            await ws.send(json.dumps({"channel": "executions", "data": list(open_trades.values())}))
        # Ticker snapshot (latest level-1 prices)
        try:
            ticker_map = getattr(kraken, "ticker_prices", {})
            if ticker_map:
                await ws.send(json.dumps({
                    "channel": "ticker", 
                    "data": ticker_map  # Send the full ticker map with last/bid/ask prices
                }))
        except Exception:
            pass
        
        # Recent logs (last 50 lines each)
        try:
            with open("bot-log", "r") as f:
                lines = f.readlines()[-50:]
                for line in lines:
                    await ws.send(json.dumps({"channel": "system_log", "data": line}))
        except Exception:
            pass
        try:
            with open("trades-log", "r") as f:
                lines = f.readlines()[-50:]
                for line in lines:
                    await ws.send(json.dumps({"channel": "trades_log", "data": line}))
        except Exception:
            pass

        # Send initial recent statistics
        try:
            stats = await calculate_recent_stats()
            await ws.send(json.dumps({"channel": "recent_stats", "data": stats}))
        except Exception:
            pass

        # Send initial investor data
        try:
            await broadcast_investor_data()
        except Exception:
            pass

        # Keep the connection open
        while True:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("WebSocket connection cancelled")
                break
            except websockets.exceptions.ConnectionClosedError:
                logger.info("WebSocket connection closed by client")
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
    except websockets.exceptions.ConnectionClosedError:
        logger.info("WebSocket connection closed by client during initial setup")
    except Exception as e:
        logger.error(f"WebSocket error during setup: {e}")
    finally:
        # Unregister client and ensure connection is closed
        connected_clients.discard(ws)
        logger.debug("WebSocket client disconnected")
        if not ws.close:
            try:
                await ws.close(1000, "Server closing connection normally")
            except Exception:
                pass

async def broadcast_to_clients(message: dict):
    if not connected_clients:
        return
    disconnected = set()
    for ws in connected_clients:
        try:
            await ws.send(json.dumps(message))
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        connected_clients.discard(ws)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, startup_timeout=60)
