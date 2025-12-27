
import os
import psycopg2
from dotenv import load_dotenv

# Load credentials
load_dotenv("/Users/hadi/.gemini/.env")

DB_URL = os.environ.get("SUPABASE_CONNECTION_STRING")
SCHEMA_PATH = "/Users/hadi/.gemini/antigravity/experiments/Project_Mirror/schema.sql"

def apply_schema():
    if not DB_URL:
        print("Error: SUPABASE_CONNECTION_STRING not found in .env")
        return

    try:
        print(f"Connecting to Supabase Database...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        print(f"Reading schema from {SCHEMA_PATH}...")
        with open(SCHEMA_PATH, 'r') as f:
            sql = f.read()
            
        print("Applying SQL schema...")
        cur.execute(sql)
        conn.commit()
        
        print("Schema applied successfully (mirror_ tables created).")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Failed to apply schema: {e}")

if __name__ == "__main__":
    apply_schema()
