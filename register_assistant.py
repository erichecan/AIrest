import requests
import json
import os

# Configuration Path
CONFIG_FILE = "vapi_restaurant_config.json"

def register_assistant():
    print("🚀 Vapi Assistant Auto-Registrar")
    print("--------------------------------")
    
    # 1. Load Config
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ Error: {CONFIG_FILE} not found.")
        return
        
    with open(CONFIG_FILE, "r") as f:
        payload = json.load(f)
        
    print(f"✅ Loaded configuration for: {payload.get('name')}")
    
    # 2. Get API Key
    api_key = input("🔑 Please enter your Vapi Private API Key: ").strip()
    if not api_key:
        print("❌ API Key is required.")
        return

    # 3. Send Request
    url = "https://api.vapi.ai/assistant"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print("⏳ Registering Assistant with Vapi...")
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        asst_id = data.get("id")
        
        print("\n🎉 Success! Assistant Created.")
        print(f"🆔 Assistant ID: {asst_id}")
        print("--------------------------------")
        print("👉 Go to Vapi Dashboard -> Assistants, and you will see it there.")
        print("👉 Click 'Talk' to test it immediately.")
        
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    register_assistant()
