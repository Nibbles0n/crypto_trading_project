import os
import json
import time
from dotenv import load_dotenv
import discum

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_USER_TOKEN')
CHANNEL_IDS = [int(id.strip()) for id in os.getenv('DISCORD_CHANNEL_IDS', '').split(',') if id.strip()]

class SimpleDiscordBot:
    def __init__(self):
        self.bot = discum.Client(token=TOKEN, log=False)
        self.channel_ids = CHANNEL_IDS
        print(f"Monitoring channels: {self.channel_ids}")
        
        # Set up message handler
        @self.bot.gateway.command
        def handle_messages(resp):
            if resp.event.message:
                message = resp.parsed.auto()
                channel_id = message.get('channel_id')
                
                # Only process messages from the specified channels
                if int(channel_id) in self.channel_ids:
                    author = message.get('author', {}).get('username', 'Unknown')
                    content = message.get('content', '')
                    print(f"\n[{channel_id}] {author}: {content}")
                    
                    # Here you can add your signal parsing logic
                    if any(keyword in content.upper() for keyword in ['LONG', 'SHORT']):
                        print("Potential trade signal detected!")
                        # Add your signal processing logic here

    def run(self):
        print("Starting Discord bot...")
        self.bot.gateway.run(auto_reconnect=True)

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_USER_TOKEN not found in .env file")
        exit(1)
    
    if not CHANNEL_IDS:
        print("Error: No valid channel IDs found in DISCORD_CHANNEL_IDS")
        exit(1)
    
    bot = SimpleDiscordBot()
    bot.run()
