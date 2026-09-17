import urllib.request
import urllib.parse
import json
import os
from dotenv import load_dotenv
load_dotenv()

params = urllib.parse.urlencode({'filters': '[["reference_name","=","test_pytest_1789672174"],["reference_doctype","=","CRM Call Log"]]', 'fields': '["content"]'})
url = f"{os.environ['FRAPPE_BASE_URL'].rstrip('/')}/api/resource/Comment?{params}"
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
        print(f"Comments for test log: {data}")
except Exception as e:
    print("Error:", e)
