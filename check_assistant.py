import requests
import os
import json
import argparse

def check_assistant():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, help="Vapi Private API Key")
    parser.add_argument("--id", required=True, help="Assistant ID")
    args = parser.parse_args()

    url = f"https://api.vapi.ai/assistant/{args.id}"
    headers = {
        "Authorization": f"Bearer {args.key}",
        "Content-Type": "application/json"
    }

    try:
        print(f"🔍 Fetching Assistant {args.id}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Check tools
        model = data.get("model", {})
        tools = model.get("tools", [])
        
        print("\n✅ Assistant Configuration Fetched.")
        print(f"Name: {data.get('name')}")
        print(f"Model: {model.get('model')}")
        print(f"Tools Count: {len(tools)}")
        
        if tools:
            print("\n📋 Tools Found:")
            for t in tools:
                if t.get("type") == "function":
                    fname = t.get("function", {}).get("name")
                    print(f"  - [Function] {fname}")
                else:
                    print(f"  - [{t.get('type')}] {t.get('function', {}).get('name', 'unknown')}")
        else:
            print("\n❌ No tools found in 'model.tools'.")

        # Check legacy functions or other places
        if "functions" in model:
             print(f"\nExample found in 'model.functions': {len(model['functions'])}")

    except Exception as e:
        print(f"❌ Error: {e}")
        if 'response' in locals():
            print(response.text)

if __name__ == "__main__":
    check_assistant()
