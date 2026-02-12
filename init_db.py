import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Configuration
# fallback to the user provided string if env var not set
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_uUTzYB6Awd3q@ep-withered-pond-aibgxrga-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


# Sanitize URL for psycopg2
if DB_URL.startswith("postgresql+asyncpg://"):
    DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    
def init_db():
    print(f"🔌 Connecting to Database...")
    try:
        sanitized_url = DB_URL.split("?")[0]
        conn = psycopg2.connect(sanitized_url, sslmode="require")
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
        cur.execute("""
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(128);
        """)

        # NL Intents Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nl_intents (
                intent_id VARCHAR(64) PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL,
                restaurant_id INTEGER NOT NULL,
                actor_id VARCHAR(64),
                source VARCHAR(32),
                language VARCHAR(8),
                raw_text TEXT NOT NULL,
                intent_type VARCHAR(128) NOT NULL,
                confidence NUMERIC(4,3) NOT NULL,
                risk_level VARCHAR(16) NOT NULL,
                requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
                effective_start TIMESTAMPTZ,
                effective_end TIMESTAMPTZ,
                payload JSONB NOT NULL,
                validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
                status VARCHAR(32) NOT NULL DEFAULT 'parsed',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Config Changes Ledger
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_changes (
                change_id VARCHAR(64) PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL,
                restaurant_id INTEGER NOT NULL,
                intent_id VARCHAR(64) REFERENCES nl_intents(intent_id),
                action_type VARCHAR(128) NOT NULL,
                payload JSONB NOT NULL,
                previous_state JSONB NOT NULL,
                new_state JSONB NOT NULL,
                applied BOOLEAN NOT NULL DEFAULT TRUE,
                applied_at TIMESTAMPTZ,
                rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
                rolled_back_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Runtime Config Snapshots
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_snapshots (
                snapshot_id BIGSERIAL PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL,
                restaurant_id INTEGER NOT NULL,
                config JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Audit Logs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                audit_id BIGSERIAL PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL,
                restaurant_id INTEGER NOT NULL,
                actor_id VARCHAR(64),
                source VARCHAR(32),
                event_type VARCHAR(64) NOT NULL,
                detail JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Indexes for SaaS queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_nl_intents_tenant_rest_created
            ON nl_intents (tenant_id, restaurant_id, created_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_config_changes_tenant_rest_created
            ON config_changes (tenant_id, restaurant_id, created_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_rest_created
            ON orders (restaurant_id, created_at DESC);
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_source_event
            ON orders (source_event_id);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id VARCHAR(128) PRIMARY KEY,
                call_id VARCHAR(128),
                tool_name VARCHAR(128),
                response TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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
