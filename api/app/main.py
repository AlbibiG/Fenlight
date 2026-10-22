from fastapi import FastAPI
from auth import router as auth_router
from fenlight import router as fenlight_router

app = FastAPI()

# Include routers from your modules
app.include_router(auth_router)
app.include_router(fenlight_router)

'''
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"--- 422 VALIDATION ERROR ---")
    print(f"Body received: {exc.body}")
    print(f"Validation errors: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )
'''