import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY")
BASE_URL = "https://api.sarvam.ai/v1"

headers = {
    "api-subscription-key": API_KEY,
    "Content-Type": "application/json"
}

data = {
    "model": "sarvam-m", # this is model name and we can change it to sarvam-s or sarvam-l based on our requirement.
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Translate 'Hello, how are you?' into Hindi."}
    ]
}

response = requests.post(
    f"{BASE_URL}/chat/completions",
    json=data,
    headers=headers
)

print(response.status_code)
print(response.json())
