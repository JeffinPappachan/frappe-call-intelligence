import urllib.request
import urllib.parse
import json
import os
from dotenv import load_dotenv
load_dotenv()

params = urllib.parse.urlencode({'limit_page_length': 1, 'order_by': 'creation desc'})
url = f"{os.environ['FRAPPE_BASE_URL'].rstrip('/')}/api/resource/CRM%20Call%20Log?{params}"
req = urllib.request.Request(url)
req.add_header("Authorization", f"token {os.environ['FRAPPE_API_KEY']}:{os.environ['FRAPPE_API_SECRET']}")
req.add_header("Accept", "application/json")

import ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    with urllib.request.urlopen(req, context=ctx) as response:
        data = json.loads(response.read().decode())['data']
        print(f"Latest call log: {data}")
except Exception as e:
    print("Error:", e)
