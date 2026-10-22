import logging
from fastapi import FastAPI
from fenlight import router as fenlight_router

# Send the API's own messages to the journal next to uvicorn's request log. The root logger stays at
# WARNING so libraries stay quiet; fenlight also logs INFO messages such as table migrations.
logging.basicConfig(format="%(levelname)s: %(name)s: %(message)s")
logging.getLogger("fenlight").setLevel(logging.INFO)

app = FastAPI()
app.include_router(fenlight_router)
