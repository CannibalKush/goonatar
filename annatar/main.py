import os
import re
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Callable

import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request
from structlog.contextvars import bind_contextvars, clear_contextvars

from annatar import human, jackett, logging
from annatar.debrid.models import StreamLink
from annatar.debrid.providers import DebridService, get_provider
from annatar.jackett_models import SearchQuery
from annatar.stremio import Stream, StreamResponse, get_media_info
from annatar.torrent import Torrent

logging.init()
app = FastAPI()

request_id = ContextVar("request_id", default="unknown")

log = structlog.get_logger(__name__)

jackett_url: str = os.environ.get("JACKETT_URL", "http://localhost:9117")
jackett_api_key: str = os.environ.get("JACKETT_API_KEY", "")


@app.middleware("http")
async def http_mw(request: Request, call_next: Callable[[Request], Any]):
    clear_contextvars()
    rid: str = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request_id.set(rid)
    bind_contextvars(request_id=rid)
    ll = log.bind(
        method=request.method,
        query=request.url.query,
        request_id=request_id.get(),
        remote=request.client.host if request.client else None,
    )

    start_time: datetime = datetime.now()
    ll.info("http_request")
    response: Any = await call_next(request)
    process_time = f"{(datetime.now() - start_time).total_seconds():.3f}s"
    response.headers["X-Process-Time"] = process_time
    response.headers["X-Request-ID"] = str(request_id.get())
    ll.info(
        "http_response",
        duration=process_time,
        status=response.status_code,
        path=request.scope.get("route", APIRouter()).path,
    )
    return response


@app.get("/manifest.json")
async def get_manifest() -> dict[str, Any]:
    return {
        "id": "community.blockloop.annatar",
        "icon": "https://i.imgur.com/wEYQYN8.png",
        "version": "0.1.0",
        "catalogs": [],
        "resources": ["stream"],
        "types": ["movie", "series"],
        "name": "Annatar",
        "description": "Lord of Gifts. Search popular torrent sites and Debrid caches for streamable content.",
        "behaviorHints": {
            "configurable": "true",
        },
    }


@app.get("/stream/{type:str}/{id:str}.json")
async def search(
    type: str,
    id: str,
    streamService: str,
    debridApiKey: str,
    maxResults: int = 5,
) -> StreamResponse:
    if type not in ["movie", "series"]:
        raise HTTPException(
            status_code=400, detail="Invalid type. Valid types are movie and series"
        )
    if not id.startswith("tt"):
        raise HTTPException(status_code=400, detail="Invalid id. Id must be an IMDB id with tt")

    imdb_id = id.split(":")[0]
    season_episode: list[int] = [int(i) for i in id.split(":")[1:]]
    log.info("searching for media", type=type, id=id)

    media_info = await get_media_info(id=imdb_id, type=type)
    if not media_info:
        log.error("error getting media info", type=type, id=id)
        return StreamResponse(streams=[], error="Error getting media info")
    log.info("found media info", type=type, id=id, media_info=media_info.model_dump())

    q = SearchQuery(
        name=media_info.name,
        type=type,
        year=re.split(r"\D", (media_info.releaseInfo or ""))[0],
    )

    if type == "series":
        q.season = str(season_episode[0])
        q.episode = str(season_episode[1])

    torrents: list[Torrent] = await jackett.search_indexers(
        max_results=max(10, maxResults),
        jackett_url=jackett_url,
        jackett_api_key=jackett_api_key,
        search_query=q,
        imdb=int(imdb_id.replace("tt", "")),
        timeout=60,
    )

    debrid_service: DebridService = get_provider(streamService)

    links: list[StreamLink] = await debrid_service.get_stream_links(
        torrents=torrents,
        debrid_token=debridApiKey,
        season_episode=season_episode,
        max_results=maxResults,
    )

    sorted_links: list[StreamLink] = sorted(
        links,
        key=lambda x: human.sort_priority(q.name, x.name),
    )

    streams: list[Stream] = [
        Stream(
            title=media_info.name,
            url=link.url,
            name="\n".join(
                [
                    link.name,
                    f"💾{human.bytes(float(link.size))}",
                ]
            ),
        )
        for link in sorted_links
    ]
    return StreamResponse(streams=streams)
