# Wallet Configuration
my_address = "YOUR_SOLANA_WALLET_ADDRESS"
usdc_contract_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
sol_address = 'So11111111111111111111111111111111111111111'

# Trading Parameters
usdc_size = 1000  # Amount per trade in USDC
max_positions = 10  # Maximum concurrent positions
sell_at_multiple = 20  # Target profit multiplier (30x)
sell_amount_percentage = 40  # Percentage of position to sell when target hit
stop_loss_percentage = -0.1  # Stop loss at -10%

# Risk Management - Social Media Checks
drop_if_no_website = False
drop_if_no_twitter = True
drop_if_no_telegram = False
only_keep_active_websites = True

# Risk Management - Token Security
top_10_holder_percent_max = 0.7  # Max 70% held by top 10 holders
drop_if_mutable_metadata = True
drop_if_2022_token_program = True

# Do Not Trade List (frozen/problematic tokens)
do_not_trade_list = [
    "SOLANA_ADDRESS",
    "USDC_ADDRESS",
    # Add problematic token addresses
]

#Data sources
closed_positions_txt = '/Users/malcolm/MOONDEV-SniperBot/data/closed_postions.txt'
ready_to_buy_df_csv = '/Users/malcolm/MOONDEV-SniperBot/data/ready_to_buy.csv'