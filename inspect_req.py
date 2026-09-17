import requests
import os
from dotenv import load_dotenv
load_dotenv()

headers = {
    "Authorization": f"token {os.environ['FRAPPE_API_KEY']}:{os.environ['FRAPPE_API_SECRET']}",
    "Accept": "application/json"
}
url = f"{os.environ['FRAPPE_BASE_URL'].rstrip('/')}/api/resource/CRM Call Log?fields=[\"*\"]&limit_page_length=5"

res = requests.get(url, headers=headers)
print("Status:", res.status_code)
data = res.json().get('data', [])
for i, row in enumerate(data):
    print(f"\n--- Log {i} ---")
    for k, v in row.items():
        if v:
            print(f"{k}: {v}")
