"""Local read-only projection server; reads saved observations, never API keys."""
from fastapi import FastAPI
from services.console.api.gold_observation import router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)

from services.console.api.research_assessment import router as research_assessment_router
from tools.research_assessment_reader import read_assessment
app.include_router(research_assessment_router)
app.state.research_assessment_reader = read_assessment
