import asyncio
from dotenv import load_dotenv
load_dotenv()
import os
import sys
sys.path.insert(0, os.getcwd())
from src.frappe_client import FrappeCRMClient

async def main():
    client = FrappeCRMClient(os.environ['FRAPPE_BASE_URL'], os.environ['FRAPPE_API_KEY'], os.environ['FRAPPE_API_SECRET'])
    async with client._get_client() as httpx_client:
        res = await httpx_client.get(
            f"{client.base_url}/api/resource/CRM Call Log?fields=[\"*\"]&limit_page_length=50&order_by=start_time desc"
        )
        print("Status:", res.status_code)
        data = res.json().get('data', [])
        print(f"Total Call Logs found: {len(data)}")
        if data:
            print("First log keys:", list(data[0].keys()))
            for i in range(min(5, len(data))):
                print(f"Log {i}:")
                for key in ['name', 'id', 'custom_call_id', 'call_id', 'telecaller', 'agent_id', 'user', 'owner', 'custom_telecaller', 'type', 'status', 'call_type', 'duration', 'custom_duration', 'custom_lead_quality', 'custom_call_outcome', 'lead_quality', 'call_outcome', 'summary', 'custom_call_summary', 'call_summary']:
                    if key in data[i]:
                        print(f"  {key} = {data[i][key]}")
            
            print("All fields in log 0:", data[0])

asyncio.run(main())
