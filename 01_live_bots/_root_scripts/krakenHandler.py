import websockets
import requests
import time
import base64
import hashlib
import hmac
import json
import asyncio
import urllib.parse
from telegramHandler import send_telegram
from loggingHandler import logger, tradesLog
from investorManager import investor_manager
from collections import deque
from typing import Optional, Callable, Awaitable, Dict, Any

class KrakenWS:

    def __init__(self, api_key, api_secret):
        self.ready_event = asyncio.Event()
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_token = None
        self.public_ws = None
        self.private_ws = None
        self.thread = None
        self.connected = False
        self.public_ready = asyncio.Event()
        self.private_ready = asyncio.Event()
        self.private_url = "wss://ws-auth.kraken.com/v2"
        self.public_url = "wss://ws.kraken.com/v2"
        self.on_balance_update: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_trade_update: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_ohlc_update: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        # Storage
        self.balances = {}                     # latest balances by asset
        self.transactions = deque(maxlen=20)   # last 20 ledger updates
        self.executions = deque(maxlen=20)     # last 20 trade/fill events
        self.ohlc_data = {}                    # symbol -> deque of last 90 bars
        self.max_ohlc_bars = 90
        # Live best-price (level-1) ticker prices per symbol
        self.ticker_prices = {}
        # Portfolio value history (2 weeks of minute data = 20160 points)
        self.portfolio_values = deque(maxlen=20160)  # [(timestamp, value), ...]
        self.last_value_update = 0  # timestamp of last update

        # Callbacks (already typed above; keep default None)
        self.on_execution_update = None
        # Optional external callback for ticker updates
        self.on_ticker_update: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None

    def getWsToken(self):
        path = "/0/private/GetWebSocketsToken"
        url = "https://api.kraken.com" + path
        nonce = str(int(time.time() * 1000))
        data = {"nonce": nonce}

        # Kraken signing: API-Sign = base64(HMAC_SHA512(path + SHA256(nonce + postdata)))
        postdata = urllib.parse.urlencode(data)
        sha_payload = (nonce + postdata).encode()
        sha256 = hashlib.sha256(sha_payload).digest()
        message = path.encode() + sha256
        secret = base64.b64decode(self.api_secret)
        signature = hmac.new(secret, message, hashlib.sha512)
        sig_digest = base64.b64encode(signature.digest())

        headers = {"API-Key": self.api_key, "API-Sign": sig_digest.decode()}

        resp = requests.post(url, headers=headers, data=data, timeout=10)
        result = resp.json()

        # Kraken V0 returns {'error': [...]} if failure
        errors = result.get("error") or []
        if isinstance(errors, list) and errors:
            logger.error(f"GetWebSocketsToken error: {errors}")
            raise RuntimeError(f"Kraken error: {errors}")

        token = (result.get("result") or {}).get("token")
        if token:
            self.ws_token = str(token)
        else:
            logger.error(f"GetWebSocketsToken: no token in response: {result}")
            raise RuntimeError("No token received from Kraken")

        return result
    
    async def auto_refresh_token(self, tokenList, interval=800):
        while True:
            # Ensure sockets are ready before attempting to subscribe
            try:
                await self.public_ready.wait()
                await self.private_ready.wait()
                self.getWsToken()
                await self.subscribeToData(tokenList)
                logger.info("Kraken WS token refreshed.")
            except Exception:
                logger.exception("Error refreshing Kraken WS token or subscribing")
            await asyncio.sleep(interval)

    async def connect(self):
        asyncio.create_task(self._connect_public())
        asyncio.create_task(self._connect_private())
        await self.private_ready.wait()
        await self.public_ready.wait()
        logger.info("Connected to private and public Kraken websockets")
        self.ready_event.set()

    async def close(self):
        """Gracefully close public and private websocket connections."""
        try:
            if self.public_ws is not None:
                try:
                    await self.public_ws.close()
                finally:
                    self.public_ws = None
        except Exception:
            logger.exception("Error closing public websocket")
        try:
            if self.private_ws is not None:
                try:
                    await self.private_ws.close()
                finally:
                    self.private_ws = None
        except Exception:
            logger.exception("Error closing private websocket")
        # Clear readiness so future logic won't assume connected
        self.public_ready = asyncio.Event()
        self.private_ready = asyncio.Event()
        self.ready_event = asyncio.Event()

    async def _connect_private(self):
        while True:  # Keep trying to reconnect
            try:
                self.private_ready.clear()
                async with websockets.connect(self.private_url) as ws:
                    self.private_ws = ws
                    self.private_ready.set()
                    logger.debug("Connected to private WebSocket")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Balances
                            if data.get("channel") == "balances" and self.on_balance_update:
                                try:
                                    if asyncio.iscoroutinefunction(self.on_balance_update):
                                        await self.on_balance_update(data)
                                    else:
                                        self.on_balance_update(data)
                                except Exception:
                                    logger.exception("Error in on_balance_update callback")
                            # Executions / trades
                            elif data.get("channel") == "executions" and self.on_trade_update:
                                try:
                                    if asyncio.iscoroutinefunction(self.on_trade_update):
                                        await self.on_trade_update(data)
                                    else:
                                        self.on_trade_update(data)
                                except Exception:
                                    logger.exception("Error in on_trade_update callback")

                            self._handle_private_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode message: {message}")
                        except Exception as e:
                            logger.exception(f"Error handling message: {message}")
            except websockets.exceptions.ConnectionClosedError:
                logger.debug("Private WebSocket connection closed, attempting to reconnect...")
                self.private_ready.clear()
                await asyncio.sleep(1)  # Wait before reconnecting
            except Exception as e:
                logger.exception("Error in private WebSocket connection")
                self.private_ready.clear()
                await asyncio.sleep(5)  # Longer wait on unexpected errors

    async def _connect_public(self):
        while True:  # Keep trying to reconnect
            try:
                self.public_ready.clear()
                async with websockets.connect(self.public_url) as ws:
                    self.public_ws = ws
                    self.public_ready.set()
                    logger.debug("Connected to public WebSocket")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            
                            # OHLC channel: dispatch callback with full message if provided
                            if data.get("channel") == "ohlc" and self.on_ohlc_update:
                                try:
                                    # Support async or sync callbacks
                                    if asyncio.iscoroutinefunction(self.on_ohlc_update):
                                        await self.on_ohlc_update(data)
                                    else:
                                        self.on_ohlc_update(data)
                                except Exception:
                                    logger.exception("Error in on_ohlc_update callback")

                            # Always handle internal storage updates
                            self._handle_public_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode message: {message}")
                        except Exception as e:
                            logger.exception(f"Error handling message: {message}")
            except websockets.exceptions.ConnectionClosedError:
                logger.debug("Public WebSocket connection closed, attempting to reconnect...")
                self.public_ready.clear()
                await asyncio.sleep(1)  # Wait before reconnecting
            except Exception as e:
                logger.exception("Error in public WebSocket connection")
                self.public_ready.clear()
                await asyncio.sleep(5)  # Longer wait on unexpected errors


    async def send(self, payload, private=False, max_retries=3):
        for attempt in range(max_retries):
            try:
                ws = self.private_ws if private else self.public_ws
                ready = self.private_ready if private else self.public_ready
                
                # Check connection state
                if not ws or not ready.is_set():
                    logger.info("WebSocket disconnected, reconnecting...")
                    self.private_ready.clear()
                    self.public_ready.clear()
                    await self.connect()
                    # Re-get the websocket after reconnection
                    ws = self.private_ws if private else self.public_ws
                
                await ready.wait()
                if ws:
                    try:
                        await ws.send(json.dumps(payload))
                        return
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("Connection lost during send")
                        continue
                else:
                    logger.warning("WebSocket not connected after reconnect attempt")
            except websockets.exceptions.ConnectionClosedError:
                logger.warning(f"Connection closed during send (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1)  # Wait before retry
                continue
            except Exception as e:
                logger.exception(f"Error during send (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1)
                continue
        
        raise RuntimeError(f"Failed to send after {max_retries} attempts")

    async def exitAll(self):
        payload = {"method": "cancel_all","params": {"token": self.ws_token}}
        await self.send(payload=payload, private=True)

    async def cancelOrder(self, orderID):
        payload = {"method": "cancel_order", "params": {"order_id": orderID, "token": self.ws_token}}
        await self.send(payload=payload, private=True)

    async def enterPosition(self, amount, symbol):
        limit_price = self.ticker_prices.get(symbol, {}).get('ask')
        if limit_price is None:
            logger.error(f"No ask price available for {symbol}, cannot place limit order")
            return
        payload = {
            "method": "add_order",
            "params": {"order_type": "limit", "side": "buy", "order_qty": amount, "symbol": symbol, "limit_price": limit_price, "token": self.ws_token}
        }
        logger.info(f"Entering BUY {amount:.8f} {symbol} @ limit {limit_price:.8f}")
        send_telegram(f"Entering BUY of {amount:.8f} {symbol} @ limit price {limit_price:.8f}")
        await self.send(payload=payload, private=True)

    async def exitPosition(self, amount, symbol):
        limit_price = self.ticker_prices.get(symbol, {}).get('bid')
        if limit_price is None:
            logger.error(f"No bid price available for {symbol}, cannot place limit order")
            return
        payload = {
            "method": "add_order",
            "params": {"order_type": "limit", "side": "sell", "order_qty": amount, "symbol": symbol, "limit_price": limit_price, "token": self.ws_token}
        }
        logger.info(f"Exiting BUY {amount:.8f} {symbol} @ limit {limit_price:.8f}")
        send_telegram(f"Exiting BUY of {amount:.8f} {symbol} @ limit price {limit_price:.8f}")
        await self.send(payload=payload, private=True)

    async def subscribeToData(self, tokens):
        # Subscribe to private executions (trades/fills)
        payload_exec = {
            "method": "subscribe",
            "params": {
                "channel": "executions",
                "token": self.ws_token,
                "snap_orders": False,
                "snap_trades": False
            }
        }
        await self.send(payload=payload_exec, private=True)

        # Subscribe to private balances
        payload_bal = {
            "method": "subscribe",
            "params": {
                "channel": "balances",
                "token": self.ws_token,
                "snapshot": True
            }
        }
        await self.send(payload=payload_bal, private=True)

        # Subscribe to public OHLC
        payload_ohlc = {
            "method": "subscribe",
            "params": {
                "channel": "ohlc",
                "symbol": tokens,
                "interval": 1,
                "snapshot": True
            }
        }
        await self.send(payload=payload_ohlc, private=False)

        # Subscribe to public ticker (level-1) for live prices
        payload_ticker = {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": tokens,
                "event_trigger": "trades",
                "snapshot": True
            }
        }
        await self.send(payload=payload_ticker, private=False)

    # Message Handlers

    def _handle_private_message(self, message):
        channel = message.get("channel")
        msg_type = message.get("type")
        if channel == "balances":
            if msg_type == "snapshot":
                self._handle_balance_snapshot(message.get("data", []))
            elif msg_type == "update":
                self._handle_balance_update(message.get("data", []))
        elif channel == "executions" and msg_type in ("snapshot", "update"):
            self._handle_execution_message(message.get("data", []))

    def _handle_balance_snapshot(self, data_list):
        # Only log non-zero balances for production
        non_zero = []
        for item in data_list:
            asset = item.get("asset")
            balance = float(item.get("balance", 0.0))
            self.balances[asset] = balance
            if balance > 0:
                non_zero.append(f"{asset}: {balance:.8f}")
        if non_zero:
            logger.info(f"Non-zero balances: {', '.join(non_zero)}")
            
        # Update portfolio value
        self.calculate_portfolio_value()

    def _handle_balance_update(self, data_list):
        for tx in data_list:
            asset = tx.get("asset")
            balance = tx.get("balance", 0.0)
            self.balances[asset] = balance
            self.transactions.append(tx)
            # Do not call external callbacks here; they are handled in the WS loop

    def _handle_execution_message(self, data_list):
        for report in data_list:
            self.executions.append(report)
            # Log concise trade summary instead of full report
            symbol = report.get('symbol', 'UNKNOWN')
            side = report.get('side', 'UNKNOWN')
            qty = float(report.get('last_qty', 0))
            price = float(report.get('last_price', 0))
            cost = float(report.get('cost', 0))
            status = report.get('order_status', 'UNKNOWN')
            
            if status == 'filled':
                tradesLog.info(f"Trade executed: {side.upper()} {qty:.2f} {symbol} @ {price:.8f} (${cost:.2f})")
                send_telegram(f"Trade executed: {side.upper()} {qty:.2f} {symbol} @ {price:.8f} (${cost:.2f})")
            # Do not call external callbacks here; they are handled in the WS loop

    # Public Message Handler
    def _handle_public_message(self, message):
        channel = message.get("channel")
        msg_type = message.get("type")
        if channel == "ohlc" and msg_type in ("snapshot", "update"):
            self._handle_ohlc_message(message.get("data", []))
        elif channel == "ticker" and msg_type in ("snapshot", "update"):
            # Update internal ticker map and optionally call external callback
            self._handle_ticker_message(message.get("data", []))

    def _handle_ohlc_message(self, data_list):
        for candle in data_list:
            symbol = candle.get("symbol")
            if symbol not in self.ohlc_data:
                self.ohlc_data[symbol] = deque(maxlen=self.max_ohlc_bars)
            self.ohlc_data[symbol].append(candle)
        # External callbacks for OHLC are dispatched in the WS loop with full message

    def _extract_prices_from_ticker_item(self, item: Dict[str, Any]) -> Optional[Dict[str, float]]:
        # Extract both ask and bid prices from ticker update
        try:
            ask = item.get("ask")
            bid = item.get("bid")
            if ask is not None and bid is not None:
                return {"ask": float(ask), "bid": float(bid)}
        except (ValueError, TypeError):
            pass
        return None

    def calculate_portfolio_value(self) -> float:
        """Calculate total portfolio value in USD using current balances and prices."""
        total_value = 0.0

        try:
            # Add USD balance directly
            usd_balance = float(self.balances.get("USD", self.balances.get("ZUSD", 0.0)))
            total_value += usd_balance

            # Add value of other assets
            for asset, balance in self.balances.items():
                if asset in ("USD", "ZUSD"):
                    continue

                try:
                    balance_float = float(balance)
                    if balance_float <= 0:
                        continue

                    # Try to get price from ticker
                    pair = f"{asset}/USD"
                    prices = self.ticker_prices.get(pair, {})
                    price = prices.get('ask')

                    if price:
                        asset_value = balance_float * price
                        total_value += asset_value
                except (ValueError, TypeError):
                    continue

            # Update share price based on new AUM
            investor_manager.calculate_share_price(total_value)

            # Update history if enough time has passed (every minute)
            current_time = int(time.time())
            if current_time - self.last_value_update >= 60:
                self.portfolio_values.append((current_time, total_value))
                self.last_value_update = current_time

                # Record snapshot for share price tracking
                investor_manager.record_snapshot(total_value, 0.0, 0.0)  # Fees and PnL will be updated separately

            return total_value

        except Exception as e:
            logger.exception("Error calculating portfolio value")
            return 0.0


    def _handle_ticker_message(self, data_list):
        price_updated = False
        for item in data_list:
            try:
                symbol = item.get("symbol")
                if not symbol:
                    continue
                prices = self._extract_prices_from_ticker_item(item)
                if prices is None:
                    continue

                # Store latest prices
                old_prices = self.ticker_prices.get(symbol)
                self.ticker_prices[symbol] = prices
                if old_prices != prices:
                    price_updated = True

                # Call optional external async callback if present
                if self.on_ticker_update:
                    try:
                        if asyncio.iscoroutinefunction(self.on_ticker_update):
                            # Schedule but don't await to avoid blocking
                            asyncio.create_task(self.on_ticker_update({
                                "symbol": symbol,
                                "ask": prices.get("ask"),
                                "bid": prices.get("bid"),
                                "portfolio_value": self.calculate_portfolio_value()
                            }))
                        else:
                            self.on_ticker_update({
                                "symbol": symbol,
                                "ask": prices.get("ask"),
                                "bid": prices.get("bid"),
                                "portfolio_value": self.calculate_portfolio_value()
                            })
                    except Exception:
                        logger.exception("Error in on_ticker_update callback")
            except Exception:
                logger.exception("Error handling ticker item")

        # Update portfolio value if any prices changed
        if price_updated:
            self.calculate_portfolio_value()
