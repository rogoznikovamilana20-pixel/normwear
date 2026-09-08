import urllib.request

try:
    urllib.request.urlopen("https://normwear-shop.onrender.com/healthz", timeout=20).read()
except Exception:
    pass
