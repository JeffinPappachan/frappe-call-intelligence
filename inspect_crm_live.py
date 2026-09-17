import asyncio
from dotenv import load_dotenv
load_dotenv()
import os
import sys
sys.path.insert(0, os.getcwd())
from src.frappe_client import FrappeCRMClient

async def main():
    client = FrappeCRMClient(os.environ['FRAPPE_BASE_URL'], os.environ['FRAPPE_API_KEY'], os.environ['FRAPPE_API_SECRET'])
    logs = await client.get_recent_call_logs(10)
    print(f'Got {len(logs)} logs')
    if logs:
        print('Sample log:', logs[0])

asyncio.run(main())
