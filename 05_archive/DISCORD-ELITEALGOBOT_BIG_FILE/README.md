# EliteAlgo Trading Bot

A Discord bot that listens for trading signals from the EliteAlgo Discord server and executes trades on Kraken exchange.

## Features

- Listens for trading signals in specified Discord channels
- Parses trading signals with support for multiple take profit and stop loss levels
- Executes trades on Kraken exchange with proper risk management
- Supports position sizing based on account balance and risk percentage
- Implements stop loss and take profit orders for risk management
- Paper trading mode for testing strategies without real funds
- Comprehensive logging and error handling

## Prerequisites

- Python 3.8+
- Discord account with access to the EliteAlgo server
- Kraken API key and secret (for live trading)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/elitealgo-trading-bot.git
   cd elitealgo-trading-bot
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. Copy the `.env.example` file to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file with your configuration:
   - `DISCORD_USER_TOKEN`: Your Discord user token
   - `DISCORD_CHANNEL_IDS`: Comma-separated list of channel IDs to monitor
   - `KRAKEN_API_KEY`: Your Kraken API key (for live trading)
   - `KRAKEN_SECRET`: Your Kraken API secret (for live trading)
   - Adjust other trading parameters as needed

## Usage

1. Start the bot in paper trading mode (no real trades):
   ```bash
   python simple_bot.py
   ```

2. For live trading, set `PAPER_TRADING=false` in the `.env` file and restart the bot.

3. The bot will log its activity to `trading_bot.log` and save signals to `signals_log.json`.

## Trading Strategy

The bot implements the following trading strategy:

1. Listens for signals in the specified Discord channels
2. Parses the signal to extract entry, stop loss, and take profit levels
3. Calculates position size based on account balance and risk percentage
4. Places a market order to enter the position
5. Sets stop loss and take profit orders based on the signal
6. Monitors open positions and manages them according to the strategy

## Risk Management

- Position sizing based on account balance and risk percentage
- Stop loss orders to limit losses
- Multiple take profit levels to secure profits
- Maximum number of concurrent trades to limit exposure
- Maximum position size based on 24h trading volume

## Logs

- `trading_bot.log`: Contains detailed logs of bot activity
- `signals_log.json`: Records all trading signals and their status

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This software is for educational purposes only. Use at your own risk. The authors are not responsible for any financial losses incurred while using this software. Always test with paper trading before using real funds.
