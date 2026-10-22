from fastapi import APIRouter, HTTPException, Header, Depends
import os
import mysql.connector
from mysql.connector import pooling
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

CLIENT_API_KEY = os.environ["API_SECRET_KEY"]
DB_HOST = os.environ["DB_HOST"]
BACKEND_DB_USER = os.environ["FENLIGHT_DB_USER"]
BACKEND_DB_PASS = os.environ["FENLIGHT_DB_PASS"]

# Initialize a persistent MySQL connection pool (pool size of 5 connections)
db_pool = pooling.MySQLConnectionPool(
    pool_name="db_pool",
    pool_size=5,
    pool_reset_session=True,
    host=DB_HOST,
    user=BACKEND_DB_USER,
    password=BACKEND_DB_PASS
)

def verify_client_api_key(x_api_key: str = Header(None)):
    if not x_api_key or x_api_key != CLIENT_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing API Key")
    return True

# Example endpoint protected solely by your API key
@router.get("/api/data")
def get_database_data(authorized: bool = Depends(verify_client_api_key)):
    try:
        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Example query against your database
        cursor.execute("SELECT 1 AS status")
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return {"status": "success", "data": result}
        
    except mysql.connector.Error as err:
        logger.error(f"Database error: {err}")
        raise HTTPException(status_code=500, detail="Database operation failed")