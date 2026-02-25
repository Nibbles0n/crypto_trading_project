import os
import aiohttp
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def check_token():
    token = os.getenv('DISCORD_USER_TOKEN')
    if not token:
        print("Error: No token found in .env file")
        return

    headers = {
        'Authorization': token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://discord.com/api/v10/users/@me', headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("✅ Token is valid!")
                    print(f"Username: {data['username']}#{data['discriminator']}")
                    print(f"User ID: {data['id']}")
                else:
                    error = await resp.text()
                    print(f"❌ Token validation failed with status {resp.status}")
                    print(f"Response: {error}")
        except Exception as e:
            print(f"❌ Error checking token: {e}")

if __name__ == "__main__":
    asyncio.run(check_token())
