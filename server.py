import os
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, Request, HTTPException, Header, Depends
from pydantic import BaseModel
from rapidfuzz import process, fuzz
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Vapi-Restaurant")

app = FastAPI(title="Vapi Restaurant Backend")

# --- Configuration ---
DB_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_uUTzYB6Awd3q@ep-withered-pond-aibgxrga-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require")

# In-Memory Cache (Simulating Restaurant specific loading)
# For MVP, we load Restaurant ID = 1
CURRENT_RESTAURANT_ID = 1
ITEMS_DB = {}
SEARCH_INDEX = []
RESTAURANT_INFO = {"tax_rate": 0.13}

def load_data_from_db():
    global ITEMS_DB, SEARCH_INDEX, RESTAURANT_INFO
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Load Restaurant Info
        cur.execute("SELECT name FROM restaurants WHERE id = %s", (CURRENT_RESTAURANT_ID,))
        res = cur.fetchone()
        if res:
            logger.info(f"Loaded Restaurant: {res['name']}")
            RESTAURANT_INFO["name_en"] = res['name']
        
        # Load Menu Items
        cur.execute("SELECT * FROM menu_items WHERE restaurant_id = %s", (CURRENT_RESTAURANT_ID,))
        rows = cur.fetchall()
        
        new_items_db = {}
        new_search_index = []
        
        for row in rows:
            # Convert decimal to float
            row['price'] = float(row['price'])
            new_items_db[row['id']] = row
            
            # Build search string
            keywords = row.get('keywords') or []
            search_text = f"{row['name_en']} {row['name_zh']} {' '.join(keywords)}".lower()
            new_search_index.append((search_text, row['id']))
            
        ITEMS_DB = new_items_db
        SEARCH_INDEX = new_search_index
        logger.info(f"Loaded {len(ITEMS_DB)} menu items from DB.")
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"DB Load Error: {e}")

# Load on startup
load_data_from_db()

# --- Session Management (In-Memory for MVP) ---
sessions: Dict[str, Dict[str, Any]] = {}

def get_session(call_id: str) -> Dict[str, Any]:
    if not call_id:
        return {}
    if call_id not in sessions:
        sessions[call_id] = {
            "cart": [], 
            "fulfillment": {}, 
            "lang": "en",
            "created_at": datetime.now().isoformat()
        }
    return sessions[call_id]

# --- Vapi Request Models ---
class ToolCall(BaseModel):
    id: str
    function: Dict[str, Any]
    type: str = "function"

class VapiPayload(BaseModel):
    message: Dict[str, Any]

# --- Helper Functions ---
def format_price(amount: float) -> str:
    return f"${amount:.2f}"

def calculate_totals(cart: List[Dict[str, Any]]):
    subtotal = sum(item["price"] * item["qty"] for item in cart)
    tax = subtotal * RESTAURANT_INFO.get("tax_rate", 0.13)
    total = subtotal + tax
    return subtotal, tax, total

def save_order_to_db(order_id: str, cart: list, total: float, phone: str = "Unknown"):
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orders (id, restaurant_id, customer_phone, items, total, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            order_id, 
            CURRENT_RESTAURANT_ID, 
            phone, 
            json.dumps(cart), 
            total, 
            "confirmed"
        ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Order {order_id} saved to DB.")
    except Exception as e:
        logger.error(f"Failed to save order to DB: {e}")

# --- API Routes ---

@app.get("/health")
async def health_check():
    return {"status": "ok", "db_connected": len(ITEMS_DB) > 0}

@app.post("/webhook")
async def vapi_webhook(request: Request):
    """
    Main entry point for Vapi.
    """
    call_id = request.headers.get("x-vapi-call-id")
    body = await request.json()
    message = body.get("message", {})
    message_type = message.get("type")
    
    # Extract Call info for later use
    customer_phone = "Unknown"
    if message_type == "tool-calls":
        call_obj = message.get("call", {})
        if not call_id:
            call_id = call_obj.get("id", "unknown_call")
        # Try to get customer phone from Vapi customer object
        customer_phone = call_obj.get("customer", {}).get("number", "Unknown")

    if message_type == "tool-calls":
        session = get_session(call_id)
        
        tool_calls = message.get("toolCalls", [])
        results = []
        
        for tool in tool_calls:
            function_name = tool.get("function", {}).get("name")
            args = tool.get("function", {}).get("arguments", {})
            call_tool_id = tool.get("id")
            
            logger.info(f"[{call_id}] Executing {function_name} args={args}")
            
            result_content = ""
            
            try:
                # --- Tool Routing ---
                if function_name == "search_menu":
                    query = args.get("query", "")
                    lang = args.get("lang", "en")
                    session["lang"] = lang 
                    
                    # Fuzzy Search
                    matches = process.extract(query.lower(), [x[0] for x in SEARCH_INDEX], scorer=fuzz.partial_ratio, limit=3, score_cutoff=40)
                    
                    found_items = []
                    for match in matches:
                        idx = match[2]
                        item_id = SEARCH_INDEX[idx][1]
                        item = ITEMS_DB[item_id]
                        found_items.append({
                            "id": item["id"],
                            "name": item["name_zh"] if lang == "zh" else item["name_en"],
                            "price": item["price"],
                            "score": match[1]
                        })
                    
                    if not found_items:
                        result_content = json.dumps({"status": "no_match", "message": "No items found."})
                    else:
                        result_content = json.dumps({"status": "success", "matches": found_items})

                elif function_name == "add_item":
                    item_id = args.get("item_id")
                    qty = int(args.get("qty", 1))
                    notes = args.get("notes", "")
                    
                    if item_id in ITEMS_DB:
                        item = ITEMS_DB[item_id]
                        cart_item = {
                            "id": item["id"],
                            "name_en": item["name_en"],
                            "name_zh": item["name_zh"],
                            "price": item["price"],
                            "qty": qty,
                            "notes": notes
                        }
                        session["cart"].append(cart_item)
                        _, _, total = calculate_totals(session["cart"])
                        
                        msg = f"Added {qty}x {item['name_en']}."
                        if session['lang'] == 'zh':
                            msg = f"已添加 {qty}份 {item['name_zh']}。"
                            
                        result_content = json.dumps({
                            "status": "success", 
                            "message": msg,
                            "cart_count": len(session["cart"]),
                            "current_total": format_price(total)
                        })
                    else:
                        result_content = json.dumps({"status": "error", "message": "Item ID not found."})

                elif function_name == "get_order_summary":
                    lang = args.get("lang", "en")
                    cart = session["cart"]
                    
                    if not cart:
                        msg = "Your cart is empty." if lang == "en" else "您的购物车是空的。"
                        result_content = msg
                    else:
                        subtotal, tax, total = calculate_totals(cart)
                        lines = []
                        if lang == "zh":
                            lines.append("您目前的订单包括：")
                            for item in cart:
                                note_str = f" ({item['notes']})" if item.get('notes') else ""
                                lines.append(f"{item['qty']}份 {item['name_zh']}{note_str}")
                            lines.append(f"总计: {format_price(total)} (含税)")
                        else:
                            lines.append("You have ordered:")
                            for item in cart:
                                note_str = f" ({item['notes']})" if item.get('notes') else ""
                                lines.append(f"{item['qty']}x {item['name_en']}{note_str}")
                            lines.append(f"Total: {format_price(total)} (with tax)")
                        result_content = "\n".join(lines)

                elif function_name == "submit_order":
                    payment_method = args.get("payment_method", "pay_at_store")
                    cart = session["cart"]
                    
                    if not cart:
                        result_content = "Cart is empty."
                    else:
                        order_id = f"ORD-{int(datetime.now().timestamp())}"
                        subtotal, tax, total = calculate_totals(cart)
                        
                        # 1. Save to DB (Persistent Storage)
                        save_order_to_db(order_id, cart, total, customer_phone)
                        
                        # 2. Send SMS (Instant Notification)
                        items_text = "\n".join([f"{i['qty']}x {i['name_en']}" for i in cart])
                        sms_body = (
                            f"[New Order] #{order_id}\n"
                            f"Total: {format_price(total)}\n"
                            f"Items:\n{items_text}"
                        )
                        
                        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
                        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
                        twilio_from = os.getenv("TWILIO_PHONE_NUMBER")
                        store_phone = os.getenv("STORE_PHONE_NUMBER") 
                        
                        if twilio_sid:
                            try:
                                from twilio.rest import Client
                                client = Client(twilio_sid, twilio_token)
                                client.messages.create(body=sms_body, from_=twilio_from, to=store_phone)
                            except Exception as e:
                                logger.error(f"SMS Error: {e}")

                        session["cart"] = [] # Clear Cart
                        
                        result_content = json.dumps({
                            "status": "success",
                            "order_id": order_id,
                            "message_en": f"Order {order_id} confirmed. SMS sent.",
                            "message_zh": f"订单 {order_id} 已确认，厨房已收到短信。"
                        })

                elif function_name == "transfer_to_human":
                    reason = args.get("reason", "unknown")
                    lang = args.get("lang", "en")
                    transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "+15550000000")
                    msg = f"Transferring you to {transfer_number}." if lang == "en" else f"正在为您转接 {transfer_number}。"
                    result_content = msg
                    
                else:
                    result_content = f"Tool {function_name} not implemented."

            except Exception as e:
                logger.error(f"Error executing {function_name}: {e}")
                result_content = f"Error: {str(e)}"

            results.append({
                "toolCallId": call_tool_id,
                "result": result_content
            })
        
        return {"results": results}

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
