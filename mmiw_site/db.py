from __future__ import annotations
import sqlite3, os, time
DB_PATH = os.environ.get("MMIW_DB_PATH", "mmiw.db")
def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False); conn.row_factory = sqlite3.Row; return conn
def init_db():
    c=connect(); cur=c.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS cases (
        id TEXT PRIMARY KEY,status TEXT,name TEXT,dob TEXT,age_at_disappearance INTEGER,gender TEXT,
        tribal_affiliation TEXT,last_seen_date TEXT,last_seen_city TEXT,last_seen_state TEXT,geo_precision TEXT,
        public_level TEXT DEFAULT 'public',family_consent INTEGER DEFAULT 1,created_at INTEGER,updated_at INTEGER)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tips (
        id TEXT PRIMARY KEY,case_id TEXT,named INTEGER,contact TEXT,message TEXT,file_hash TEXT,created_at INTEGER)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS le_requests (
        id TEXT PRIMARY KEY,agency TEXT,contact_email TEXT,case_id TEXT,statutory_basis TEXT,scope TEXT,status TEXT,created_at INTEGER)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,actor TEXT,action TEXT,details TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        case_id TEXT,
        filename TEXT,
        stored_path TEXT,
        sha256 TEXT,
        encrypted INTEGER DEFAULT 0,
        nonce_hex TEXT,
        created_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        api_key_hash TEXT UNIQUE,
        display_name TEXT,
        role TEXT DEFAULT 'public',
        created_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS contacts (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        label TEXT,
        contact_type TEXT,
        destination TEXT,
        is_le INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 1,
        created_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS safety_profiles (
        user_id TEXT PRIMARY KEY,
        full_name TEXT,
        description TEXT,
        vehicle TEXT,
        emergency_note TEXT,
        home_address TEXT,
        tracking_enabled INTEGER DEFAULT 0,
        checkin_window_sec INTEGER DEFAULT 90,
        location_retention_days INTEGER DEFAULT 30,
        auto_delete_if_no_case INTEGER DEFAULT 1,
        updated_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS panic_events (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        ip_hash TEXT,
        note TEXT,
        structured_note TEXT,
        lat REAL,
        lng REAL,
        status TEXT,
        checkin_deadline INTEGER,
        checked_in_at INTEGER,
        tracking_active INTEGER DEFAULT 0,
        created_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS location_points (
        id TEXT PRIMARY KEY,
        panic_event_id TEXT,
        user_id TEXT,
        lat REAL,
        lng REAL,
        accuracy_m REAL,
        recorded_at INTEGER,
        expires_at INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS case_access (
        id TEXT PRIMARY KEY,
        case_id TEXT,
        user_id TEXT,
        access_role TEXT,
        granted_by TEXT,
        created_at INTEGER,
        UNIQUE(case_id, user_id)
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS panic_deliveries (
        id TEXT PRIMARY KEY,
        panic_event_id TEXT,
        contact_id TEXT,
        channel TEXT,
        status TEXT,
        error TEXT,
        created_at INTEGER
    )''')
    c.commit(); c.close()
def now_ts(): return int(time.time())
