import os
import urllib.parse
import urllib.request

req=urllib.request.Request('http://127.0.0.1:8765/',headers={
    'Host':urllib.parse.urlsplit(os.environ['CRM_PUBLIC_ORIGIN']).netloc,
    'X-CRM-Gateway':os.environ['CRM_GATEWAY_SECRET']})
with urllib.request.urlopen(req,timeout=3) as response:
    assert response.status==200
