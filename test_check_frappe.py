import asyncio
import httpx
from src.frappe_client import get_frappe_client
from src.config import get_settings

async def main():
    settings = get_settings()
    client = get_frappe_client()

    async with httpx.AsyncClient(timeout=15.0) as http_client:
        url = f"{client.base_url}/api/resource/CRM Call Log"
        params = {
            "fields": '["*"]',
            "limit_page_length": 5,
            "order_by": "creation desc"
        }
        res = await http_client.get(url, headers=client._get_headers(), params=params)
        res.raise_for_status()

        call_logs = res.json().get("data", [])
        for cl in call_logs:
            print("Call Log:", cl.get("name"))
            print(" - Caller/Reference:", cl.get("reference_docname"))
            print(" - From:", cl.get("from"))
            print(" - To:", cl.get("to"))
            print(" - Receiver (Agent):", cl.get("receiver"))
            print(" - Type:", cl.get("type"))
            print(" - Start Time:", cl.get("start_time"))
            print(" - End Time:", cl.get("end_time"))
            print(" - Duration:", cl.get("duration"))
            print(" - Status:", cl.get("status"))

        print("\nChecking tasks...")
        url_tasks = f"{client.base_url}/api/resource/CRM Task"
        params_tasks = {
            "fields": '["*"]',
            "limit_page_length": 2,
            "order_by": "creation desc"
        }
        res_tasks = await http_client.get(url_tasks, headers=client._get_headers(), params=params_tasks)
        tasks = res_tasks.json().get("data", [])
        for t in tasks:
            print("Task:", t.get("name"))
            print(" - Assigned To:", t.get("assigned_to"))
            print(" - Ref Docname:", t.get("reference_docname"))
            print(" - Due Date:", t.get("due_date"))

if __name__ == "__main__":
    asyncio.run(main())
