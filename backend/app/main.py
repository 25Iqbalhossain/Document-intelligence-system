"""FastAPI application: routers, security headers, request IDs, uniform error bodies."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, documents, invoices, reconciliation, search, system
from app.core.config import get_settings

log = logging.getLogger("docint.api")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Document Intelligence API", version="0.2.0",
                  docs_url=None if s.environment == "production" else "/docs",
                  redoc_url=None, openapi_url=None if s.environment == "production" else "/openapi.json")
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers.update({"X-Request-ID": rid, "X-Content-Type-Options": "nosniff",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
        return response

    for r in (system.router, auth.router, documents.router, invoices.router, reconciliation.router,
              search.router):
        app.include_router(r, prefix="" if r is system.router else "/api/v1")
    return app


app = create_app()
