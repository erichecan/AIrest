import os
import json
import psycopg2
from urllib.parse import urlparse

# Configuration
# fallback to the user provided string if env var not set
DB_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_uUTzYB6Awd3q@ep-withered-pond-aibgxrga-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require")

def init_db():
    print(f"🔌 Connecting to Database...")
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # 1. Create Tables
        print("🛠 Creating Tables...")
        
        # Restaurants Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS restaurants (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                vapi_assistant_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Menu Items Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id VARCHAR(50) PRIMARY KEY, -- using string IDs from JSON for now
                restaurant_id INTEGER REFERENCES restaurants(id),
                category VARCHAR(100),
                name_en VARCHAR(255),
                name_zh VARCHAR(255),
                price DECIMAL(10, 2),
                keywords TEXT[], -- Array of strings
                modifiers JSONB
            );
        """)

        # Orders Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id VARCHAR(50) PRIMARY KEY,
                restaurant_id INTEGER REFERENCES restaurants(id),
                customer_phone VARCHAR(50),
                items JSONB, -- Store full cart snapshot
                total DECIMAL(10, 2),
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
        print("✅ Tables Created.")

        # 2. Seed Data (Congee Queen)
        print("🌱 Seeding Data...")
        
        # Check if restaurant exists
        cur.execute("SELECT id FROM restaurants WHERE name = %s", ('Congee Queen (Markham)',))
        res = cur.fetchone()
        
        if not res:
            cur.execute("""
                INSERT INTO restaurants (name, phone) 
                VALUES (%s, %s) RETURNING id
            """, ('Congee Queen (Markham)', '+19059488188'))
            restaurant_id = cur.fetchone()[0]
            print(f"   -> Created Restaurant ID: {restaurant_id}")
        else:
            restaurant_id = res[0]
            print(f"   -> Found existing Restaurant ID: {restaurant_id}")

        # Load Menu JSON
        with open("menu.json", "r") as f:
            menu_data = json.load(f)
            
        # Insert Menu Items
        for item in menu_data["items"]:
            # Check exist
            cur.execute("SELECT id FROM menu_items WHERE id = %s", (item["id"],))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO menu_items (id, restaurant_id, category, name_en, name_zh, price, keywords)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    item["id"], 
                    restaurant_id, 
                    item["category"], 
                    item["name_en"], 
                    item["name_zh"], 
                    item["price"], 
                    item.get("keywords", [])
                ))
                print(f"      + Added {item['name_en']}")
            else:
                 # Update keywords just in case
                cur.execute("""
                    UPDATE menu_items SET keywords = %s WHERE id = %s
                """, (item.get("keywords", []), item["id"]))
                
        conn.commit()
        print("✅ Data Seeded Successfully.")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Database Error: {e}")

if __name__ == "__main__":
    init_db()
