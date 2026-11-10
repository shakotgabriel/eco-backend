"""One-time script to provision MTN MoMo sandbox credentials."""
import uuid
import requests

SUBSCRIPTION_KEY = "15f70d5f279e4f2389f2ec4417a332f4"
BASE = "https://sandbox.momodeveloper.mtn.com"
API_USER = str(uuid.uuid4())

print("1. Creating API User...")
resp = requests.post(
    f"{BASE}/v1_0/apiuser",
    headers={
        "X-Reference-Id": API_USER,
        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
        "Content-Type": "application/json",
    },
    json={"providerCallbackHost": "https://webhook.site"},
    timeout=60,
)
print(f"   Status: {resp.status_code}")

if resp.status_code == 201:
    print("2. Getting API Key...")
    resp2 = requests.post(
        f"{BASE}/v1_0/apiuser/{API_USER}/apikey",
        headers={"Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY},
        timeout=60,
    )
    print(f"   Status: {resp2.status_code}")
    if resp2.status_code == 201:
        api_key = resp2.json().get("apiKey", "")
        print()
        print(f"API_USER={API_USER}")
        print(f"API_KEY={api_key}")
        print(f"SUBSCRIPTION_KEY={SUBSCRIPTION_KEY}")
    else:
        print(f"   Error: {resp2.text}")
else:
    print(f"   Error: {resp.text}")
