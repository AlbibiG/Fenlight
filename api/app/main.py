import logging
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fenlight import router as fenlight_router

# Send the API's own messages to the journal next to uvicorn's request log. The root logger stays at
# WARNING so libraries stay quiet; fenlight also logs INFO messages such as table migrations.
logging.basicConfig(format="%(levelname)s: %(name)s: %(message)s")
logging.getLogger("fenlight").setLevel(logging.INFO)

app = FastAPI()
# compress larger responses for clients that accept it (Fen Light does); matters most over a remote connection
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.include_router(fenlight_router)
