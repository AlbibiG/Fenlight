from fastapi import HTTPException, Header
import logging
import os
import secrets
import threading
import api_keys

logger = logging.getLogger(__name__)
KEYS_FILE = os.environ.get("API_KEYS_FILE", api_keys.DEFAULT_FILE)
_loaded = {"mtime": None, "keys": []}
_loaded_lock = threading.Lock()

def current_keys():
    """The named keys, reread whenever the file changes, so fenlight-api-keys works without a restart.
    Installs from before named keys may still have their single key in API_SECRET_KEY: it counts as "default"."""
    try:
        mtime = os.stat(KEYS_FILE).st_mtime_ns
    except FileNotFoundError:
        mtime = None
    with _loaded_lock:
        if mtime != _loaded["mtime"]:
            try:
                _loaded["keys"] = api_keys.load(KEYS_FILE) if mtime else []
            except (OSError, ValueError, KeyError) as e:
                # keep the keys read before rather than locking every device out over a broken file
                logger.error("Can't read the API keys in %s, still using the ones read before: %s", KEYS_FILE, e)
            _loaded["mtime"] = mtime
        keys = list(_loaded["keys"])
    legacy_key = os.environ.get("API_SECRET_KEY")
    if legacy_key and not any(entry["key"] == legacy_key for entry in keys):
        keys.append({"name": "default", "key": legacy_key})
    return keys

def verify_client_api_key(x_api_key: str = Header(None)):
    """The name of the key the request was made with"""
    if x_api_key:
        provided = x_api_key.encode()
        for entry in current_keys():
            if secrets.compare_digest(provided, entry["key"].encode()):
                return entry["name"]
    # 401 is the status the Fen Light client checks for to tell the user their API key is wrong
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
