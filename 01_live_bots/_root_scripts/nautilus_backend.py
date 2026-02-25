```python
"""
nautilus_backend.py

A simple NautilusTrader backend that builds a TradingNode, registers one ExecTester
strategy per configured token (instrument), and exposes an async submit_order(...) method
that other code (e.g. a FastAPI webhook) can call to execute an order.

NOTE:
- This example uses the ExecTester strategy from the test kit as the per-instrument
  "executor" because it already exposes convenient submit helpers and integrates with
  the Nautilus strategy submission flow. You can replace ExecTester with your own
  Strategy subclass if you prefer.
- Run this backend in the same process / same asyncio event loop as the FastAPI app
  (the example webhook_server.py shows how to do that).
- Adapt the exchange adapter, API keys and environment to your needs.
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional

from nautilus_trader.adapters.kraken import KRAKEN, KrakenDataClientConfig, KrakenExecClientConfig
from nautilus_trader.adapters.kraken import KrakenLiveDataClientFactory, KrakenLiveExecClientFactory
from nautilus_trader.adapters.kraken import KrakenEnvironment, KrakenProductType
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.test_kit.strategies.tester_exec import ExecTester, ExecTesterConfig


class NautilusBackend:
    """
    Nautilus backend that:
    - builds a TradingNode
    - creates one ExecTester per token/instrument and registers it on the node
    - exposes an async submit_order(token, side, qty, price, order_type) method
      which creates an order with the strategy's order_factory and calls submit_order(...)
    """

    def __init__(
        self,
        tokens: list[str],
        product_type: KrakenProductType = KrakenProductType.SPOT,
        token_qty_map: Optional[dict[str, Decimal]] = None,
        kraken_environment: KrakenEnvironment | None = None,
    ) -> None:
        # Basic configuration (use sensible defaults; adapt for your environment)
        if kraken_environment is None:
            if product_type == KrakenProductType.SPOT:
                kraken_environment = KrakenEnvironment.MAINNET
            else:
                kraken_environment = KrakenEnvironment.TESTNET

        self.node_config = TradingNodeConfig(
            trader_id="WEBHOOK-TRADER-001",
            logging=LoggingConfig(
                log_level="INFO",
                log_level_file="DEBUG",
                log_colors=True,
                use_pyo3=True,
            ),
            exec_engine=LiveExecEngineConfig(
                reconciliation=True,
                open_check_interval_secs=5.0,
                open_check_open_only=False,
                purge_closed_orders_interval_mins=1,
                purge_closed_orders_buffer_mins=0,
                purge_closed_positions_interval_mins=1,
                purge_closed_positions_buffer_mins=0,
                purge_account_events_interval_mins=1,
                purge_account_events_lookback_mins=0,
                purge_from_database=False,
                graceful_shutdown_on_exception=True,
            ),
            data_clients={
                KRAKEN: KrakenDataClientConfig(
                    api_key=None,
                    api_secret=None,
                    environment=kraken_environment,
                    product_types=(product_type,),
                    instrument_provider=InstrumentProviderConfig(load_all=True),
                )
            },
            exec_clients={
                KRAKEN: KrakenExecClientConfig(
                    api_key=None,
                    api_secret=None,
                    environment=kraken_environment,
                    product_types=(product_type,),
                    instrument_provider=InstrumentProviderConfig(load_all=True),
                )
            },
        )

        # Create node
        self.node = TradingNode(config=self.node_config)

        # Register Kraken factories
        self.node.add_data_client_factory(KRAKEN, KrakenLiveDataClientFactory)
        self.node.add_exec_client_factory(KRAKEN, KrakenLiveExecClientFactory)

        # Strategy instances per token
        self.strategies: Dict[str, ExecTester] = {}
        self.tokens = tokens
        self.product_type = product_type
        self.token_qty_map = token_qty_map or {}

        # fill symbol mapping depending on product type
        if product_type == KrakenProductType.SPOT:
            self._symbol_for = lambda t: f"{t}/USDT"
        else:
            self._symbol_for = lambda t: f"PI_{t}USD"

        # Internal running task for node
        self._node_task: Optional[asyncio.Task] = None

    def build(self) -> None:
        """
        Create ExecTester strategies for each token and build node clients.
        Must be called before start().
        """
        # create a strategy per token (ExecTester expects one instrument)
        for t in self.tokens:
            symbol = self._symbol_for(t)
            inst_str = f"{symbol}.{KRAKEN}"
            instrument_id = InstrumentId.from_str(inst_str)

            qty = self.token_qty_map.get(t, Decimal("1"))

            strat_config = ExecTesterConfig(
                instrument_id=instrument_id,
                external_order_claims=[instrument_id],
                use_uuid_client_order_ids=True,
                subscribe_quotes=True,
                subscribe_trades=True,
                order_qty=qty,
                enable_limit_buys=True,
                enable_limit_sells=True,
                use_post_only=True,
                reduce_only_on_stop=False,
                log_data=False,
            )

            strat = ExecTester(config=strat_config)
            self.strategies[t] = strat
            self.node.trader.add_strategy(strat)

        # Build the node (construct data/exec clients)
        self.node.build()

    async def start(self) -> None:
        """
        Start the trading node asynchronously. Returns once the node start has been scheduled.
        Call this before accepting webhook requests.
        """
        if not self.node.is_built():
            raise RuntimeError("Backend must be built before start()")

        loop = self.node.get_event_loop() or asyncio.get_event_loop()
        # run the node in the same loop as caller
        # node.run_async() will not return until stop; schedule as background task
        self._node_task = loop.create_task(self.node.run_async())

        # Wait a short while for strategies/instruments to be loaded
        # (a real deployment would wait for instrument load events)
        await asyncio.sleep(1.0)

    async def stop(self) -> None:
        """
        Stop the trading node gracefully.
        """
        if self.node and self.node.is_running():
            await self.node.stop_async()
        if self._node_task:
            await asyncio.wait_for(self._node_task, timeout=10.0)

    async def submit_order(
        self,
        token: str,
        side: str,
        qty: Decimal,
        price: Optional[Decimal] = None,
        order_type: str = "market",
    ) -> dict:
        """
        Submit an order for the given token.

        Parameters
        ----------
        token : str
            Token symbol, e.g. "ETH"
        side : str
            "buy" or "sell"
        qty : Decimal
            Quantity in base units (will be converted using the instrument.make_qty)
        price : Decimal | None
            Limit price for limit orders; None for market orders
        order_type : str
            "market" or "limit"

        Returns a dict with a simple 'accepted'/'error' payload.
        """

        if token not in self.strategies:
            return {"status": "error", "reason": f"token {token} not configured"}

        strat = self.strategies[token]

        # the strategy must have loaded its instrument metadata (instrument attr)
        instrument = getattr(strat, "instrument", None)
        if instrument is None:
            return {"status": "error", "reason": "instrument not loaded yet"}

        # Map side
        side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        try:
            # Create order via strategy's order_factory
            if order_type.lower() == "market":
                # Note: ExecTester / Strategy order_factory.market(...) signature may accept
                # time_in_force, etc. Adjust per your version of NautilusTrader.
                order = strat.order_factory.market(
                    instrument_id=strat.config.instrument_id,
                    order_side=side_enum,
                    quantity=instrument.make_qty(qty),
                    time_in_force=TimeInForce.FOK,
                )
            elif order_type.lower() == "limit":
                if price is None:
                    return {"status": "error", "reason": "limit order requires price"}
                order = strat.order_factory.limit(
                    instrument_id=strat.config.instrument_id,
                    order_side=side_enum,
                    price=instrument.make_price(price),
                    quantity=instrument.make_qty(qty),
                )
            else:
                return {"status": "error", "reason": f"unknown order_type: {order_type}"}

            # Submit order - synchronous method on the strategy.
            # This will route the command into NautilusTrader's execution flow.
            strat.submit_order(order)

            return {"status": "accepted", "instrument": str(strat.config.instrument_id), "client_order_id": order.client_order_id}
        except Exception as exc:
            # In production, log details and return structured error info
            return {"status": "error", "reason": str(exc)}


# If run directly for quick manual testing:
if __name__ == "__main__":
    import os
    import uvicorn

    # Example tokens to trade
    tokens = ["ETH", "BTC", "LTC", "XRP", "ADA"]
    backend = NautilusBackend(tokens=tokens, product_type=KrakenProductType.SPOT)

    async def _run():
        backend.build()
        await backend.start()
        print("Backend started - run your app and POST webhooks now")
        # Keep running until stopped manually
        while True:
            await asyncio.sleep(60)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("Stopping backend")
        try:
            asyncio.run(backend.stop())
        except Exception:
            pass