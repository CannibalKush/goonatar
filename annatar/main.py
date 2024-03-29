from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from annatar import instrumentation, logging, middleware, web
from annatar.api import search, stremio

logging.init()
instrumentation.init()

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    instrumentation.shutdown()
    log.info("shutting down")


app = FastAPI(title="Annatar", version="0.1.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

# XXX These are executed in reverse order
app.add_middleware(middleware.Metrics)
app.add_middleware(middleware.RequestLogger)
app.add_middleware(middleware.RequestID)

app.add_route("/metrics", instrumentation.metrics_handler)


# handle CORS preflight requests
@app.options("/{rest_of_path:path}")
async def preflight_handler() -> Response:
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
        },
    )


# set CORS headers
@app.middleware("http")
async def add_CORS_header(request: Request, call_next: Any):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return response


app.include_router(stremio.router)
app.include_router(search.router)
app.include_router(web.router)
