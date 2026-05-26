import os
import psycopg2
from psycopg2.extras import Json
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# We still use Supabase Client for S3 storage upload
_supabase_client = None

def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL or SUPABASE_KEY not found in environment")
        _supabase_client = create_client(url, key)
        
        # Ensure the storage bucket exists
        try:
            _supabase_client.storage.create_bucket("court-documents", options={"public": True})
            print("Verified/Created Supabase storage bucket 'court-documents'")
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"Note on bucket verification: {e}")
                
    return _supabase_client

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not found in environment variables")
    # Use Transaction Pooler port (6543) and add sslmode/connect_timeout
    base_url = db_url.replace(":5432/", ":6543/")
    sep = "&" if "?" in base_url else "?"
    db_url_final = f"{base_url}{sep}sslmode=require&connect_timeout=15"
    return psycopg2.connect(db_url_final)

def upload_pdf_to_storage(local_path: str, storage_path: str) -> str:
    """
    Uploads a file to Supabase 'court-documents' bucket.
    Returns the public download URL.
    """
    supabase = get_supabase()
    bucket_name = "court-documents"
    
    print(f"Uploading {local_path} to storage path '{storage_path}'...")
    try:
        with open(local_path, "rb") as f:
            supabase.storage.from_(bucket_name).upload(
                path=storage_path,
                file=f,
                file_options={"content-type": "application/pdf", "x-upsert": "true"}
            )
        # Get public URL
        public_url = supabase.storage.from_(bucket_name).get_public_url(storage_path)
        return public_url
    except Exception as e:
        print(f"Failed to upload file to Supabase storage: {e}")
        try:
            return supabase.storage.from_(bucket_name).get_public_url(storage_path)
        except:
            raise e

def upsert_case(case_data: dict) -> str:
    """
    Upserts a case record in bdc.cases using direct psycopg2 database connection.
    Bypasses PostgREST exposed schema restrictions.
    Returns the database UUID of the case.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    query = """
    INSERT INTO bdc.cases (
        cnr, establishment_code, case_type, case_year, filing_no, filing_date, reg_no, reg_date,
        case_status, first_hearing, next_hearing, stage, court_name, judge,
        petitioners, petitioner_adv, respondents, respondent_adv,
        police_station, fir_number, fir_year, acts_json, page_pdf_url
    ) VALUES (
        %(cnr)s, %(establishment_code)s, %(case_type)s, %(case_year)s, %(filing_no)s, %(filing_date)s, %(reg_no)s, %(reg_date)s,
        %(case_status)s, %(first_hearing)s, %(next_hearing)s, %(stage)s, %(court_name)s, %(judge)s,
        %(petitioners)s, %(petitioner_adv)s, %(respondents)s, %(respondent_adv)s,
        %(police_station)s, %(fir_number)s, %(fir_year)s, %(acts_json)s, %(page_pdf_url)s
    ) ON CONFLICT (cnr) DO UPDATE SET
        establishment_code = EXCLUDED.establishment_code,
        case_type = EXCLUDED.case_type,
        case_year = EXCLUDED.case_year,
        filing_no = EXCLUDED.filing_no,
        filing_date = EXCLUDED.filing_date,
        reg_no = EXCLUDED.reg_no,
        reg_date = EXCLUDED.reg_date,
        case_status = EXCLUDED.case_status,
        first_hearing = EXCLUDED.first_hearing,
        next_hearing = EXCLUDED.next_hearing,
        stage = EXCLUDED.stage,
        court_name = EXCLUDED.court_name,
        judge = EXCLUDED.judge,
        petitioners = EXCLUDED.petitioners,
        petitioner_adv = EXCLUDED.petitioner_adv,
        respondents = EXCLUDED.respondents,
        respondent_adv = EXCLUDED.respondent_adv,
        police_station = EXCLUDED.police_station,
        fir_number = EXCLUDED.fir_number,
        fir_year = EXCLUDED.fir_year,
        acts_json = EXCLUDED.acts_json,
        page_pdf_url = EXCLUDED.page_pdf_url,
        last_synced = NOW()
    RETURNING id;
    """
    
    # Wrap acts_json in psycopg2 Json wrapper
    case_data_copy = case_data.copy()
    if "acts_json" in case_data_copy:
        case_data_copy["acts_json"] = Json(case_data_copy["acts_json"])
        
    try:
        cur.execute(query, case_data_copy)
        case_uuid = cur.fetchone()[0]
        conn.commit()
        return case_uuid
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

def sync_case_history(case_id: str, history_list: list):
    """
    Deletes existing history for a case and inserts the new list using psycopg2.
    """
    if not case_id:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Clear old history
        cur.execute("DELETE FROM bdc.case_history WHERE case_id = %s;", (case_id,))
        
        # 2. Insert new history rows
        if history_list:
            insert_query = """
            INSERT INTO bdc.case_history (
                case_id, judge, business_date, hearing_date, purpose, business_text
            ) VALUES (%s, %s, %s, %s, %s, %s);
            """
            for row in history_list:
                cur.execute(insert_query, (
                    case_id,
                    row.get("judge"),
                    row.get("business_date"),
                    row.get("hearing_date"),
                    row.get("purpose"),
                    row.get("business_text")
                ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

def sync_case_orders(case_id: str, orders_list: list):
    """
    Deletes existing orders for a case and inserts the new list using psycopg2.
    """
    if not case_id:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Clear old orders
        cur.execute("DELETE FROM bdc.case_orders WHERE case_id = %s;", (case_id,))
        
        # 2. Insert new orders rows
        if orders_list:
            insert_query = """
            INSERT INTO bdc.case_orders (
                case_id, order_date, order_type, file_name, storage_path, pdf_url
            ) VALUES (%s, %s, %s, %s, %s, %s);
            """
            for row in orders_list:
                cur.execute(insert_query, (
                    case_id,
                    row.get("order_date"),
                    row.get("order_type"),
                    row.get("file_name"),
                    row.get("storage_path"),
                    row.get("pdf_url")
                ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()
