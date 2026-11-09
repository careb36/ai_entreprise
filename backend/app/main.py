from fastapi import FastAPI
from backend.app.api.v1.validation import router as validation_router
from backend.app.api.v1.sandbox import router as sandbox_router

app = FastAPI(
    title="La Fabrique Pre-QA Validation & Sandbox API",
    description="API for AST validation, code linting, and ephemeral sandbox execution.",
    version="1.0.0",
)

app.include_router(validation_router, prefix="/api/v1")
app.include_router(sandbox_router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "pre-qa-validation-api"}
