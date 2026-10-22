from fastapi import HTTPException, Header
import os
import secrets

CLIENT_API_KEY = os.environ["API_SECRET_KEY"]

def verify_client_api_key(x_api_key: str = Header(None)):
    # 401 is the status the Fen Light client checks for to tell the user their API key is wrong
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), CLIENT_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True
