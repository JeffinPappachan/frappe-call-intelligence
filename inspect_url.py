import urllib.request
import urllib.parse
import json
import os
from dotenv import load_dotenv
load_dotenv()

params = urllib.parse.urlencode({'fields': '["*"]', 'limit_page_length': 5})
url = f"{os.environ['FRAPPE_BASE_URL'].rstrip('/')}/api/resource/CRM%20Call%20Log?{params}"
req = urllib.request.Request(url)
req.add_header("Authorization", f"token {os.environ['FRAPPE_API_KEY']}:{os.environ['FRAPPE_API_SECRET']}")
req.add_header("Accept", "application/json")

try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())['data']
        for i, row in enumerate(data):
            print(f"\n--- Log {i} ---")
            for k, v in row.items():
                if v:
                    print(f"{k}: {v}")
except Exception as e:
    print(e)
