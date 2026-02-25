from solana.rpc.api import Client

# Your Helius RPC URL
rpc_url = 'https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY'

# Create a client
client = Client(rpc_url)

# Test the connection
try:
    version = client.get_version()
    print('Connection successful!')
    print(f'Solana version: {version}')
except Exception as e:
    print(f'Connection failed: {e}')