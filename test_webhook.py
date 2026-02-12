import requests
import json
import uuid

WEBHOOK_URL = "https://vapi-restaurant-backend-465934989199.us-central1.run.app/webhook"

def test_webhook():
    print(f"🚀 Testing Webhook: {WEBHOOK_URL}")
    
    # Simulate Vapi Tool Call Payload
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "customer": {"number": "+14372999568"}
            },
            "toolCalls": [
                {
                    "id": f"tool_{uuid.uuid4().hex[:10]}",
                    "type": "function",
                    "function": {
                        "name": "query_orders",
                        "arguments": {
                            "filters": {"status": ["confirmed"]},
                            "limit": 5
                        }
                    }
                }
            ]
        }
    }

    try:
        print("⏳ Sending request...")
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        try:
            data = response.json()
            print("Response JSON:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # Check results
            results = data.get("results", [])
            if results:
                print("\n✅ Tool Result:")
                for r in results:
                    print(r.get("result"))
            else:
                print("\n⚠️ No results returned.")
                
        except json.JSONDecodeError:
            print("Response Text:", response.text)

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_webhook()
