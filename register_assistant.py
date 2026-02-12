import requests
import json
import os
import argparse
import sys

# Configuration Path
CONFIG_FILE = "vapi_restaurant_config.json"

def register_assistant():
    print("🚀 Vapi Assistant Manager")
    print("-------------------------")

    # Parse Arguments
    parser = argparse.ArgumentParser(description="Create or Update Vapi Assistant")
    parser.add_argument("--key", help="Vapi Private API Key")
    parser.add_argument("--id", help="Assistant ID to update (optional)")
    args = parser.parse_args()
    
    # 1. Load Config
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ Error: {CONFIG_FILE} not found.")
        return
        
    with open(CONFIG_FILE, "r") as f:
        payload = json.load(f)
        
    print(f"✅ Loaded configuration for: {payload.get('name')}")
    
    # 2. Get API Key (Env -> Arg -> Input)
    api_key = os.environ.get("VAPI_PRIVATE_KEY") or args.key
    if not api_key:
        api_key = input("🔑 Please enter your Vapi Private API Key: ").strip()
    
    if not api_key:
        print("❌ API Key is required.")
        return

    # 3. Get Assistant ID (Env -> Arg -> Input)
    assistant_id = os.environ.get("ASSISTANT_ID") or args.id
    
    # Auto-detect ID from config if not provided
    if not assistant_id and payload.get("id"):
        print(f"ℹ️  Found Assistant ID in config: {payload.get('id')}")
        if input(f"   Use this ID to update? (Y/n): ").strip().lower() != 'n':
            assistant_id = payload.get("id")

    if not assistant_id:
        print("\nChecking for existing assistant...")
        user_input = input("🔄 Enter Assistant ID to UPDATE (or press Enter to CREATE NEW): ").strip()
        if user_input:
            assistant_id = user_input

    # Validate UUID if provided
    if assistant_id:
        import uuid
        try:
            uuid.UUID(assistant_id)
        except ValueError:
            print(f"❌ Error: '{assistant_id}' is not a valid UUID.")
            return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Remove 'id' from payload if present (API rejects it in body)
    payload.pop("id", None)

    # 4. Send Request
    if assistant_id:
        print(f"⏳ Updating existing Assistant ({assistant_id})...")
        url = f"https://api.vapi.ai/assistant/{assistant_id}"
        method = "PATCH"
    else:
        print("⏳ Creating NEW Assistant...")
        url = "https://api.vapi.ai/assistant"
        method = "POST"

    try:
        if method == "PATCH":
            response = requests.patch(url, json=payload, headers=headers)
        else:
            response = requests.post(url, json=payload, headers=headers)
            
        response.raise_for_status()
        
        data = response.json()
        new_id = data.get("id")
        
        print("\n🎉 Success!")
        print(f"🆔 Assistant ID: {new_id}")
        print("--------------------------------")
        print("👉 Go to Vapi Dashboard to verify: https://dashboard.vapi.ai/assistants")
        
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    register_assistant()
