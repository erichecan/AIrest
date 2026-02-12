import os
import psycopg2

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_uUTzYB6Awd3q@ep-withered-pond-aibgxrga-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require",
)

if DB_URL.startswith("postgresql+asyncpg://"):
    DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://")

def debug_db():
    print(f"🔌 Connecting to Database...")
    print(f"URL (masked): {DB_URL.split('@')[-1]}")
    try:
        sanitized_url = DB_URL.split("?")[0]
        conn = psycopg2.connect(sanitized_url, sslmode="require")
        cur = conn.cursor()
        
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        rows = cur.fetchall()
        print("\n📊 Tables in 'public' schema:")
        for row in rows:
            print(f" - {row[0]}")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    debug_db()
