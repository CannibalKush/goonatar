import os
from base64 import b64encode
from enum import Enum
from typing import Annotated, Any, Optional
from annatar.api.catalogs.manifest import generate_catalogs
from annatar.clients.stashdb import Sort, get_scene, get_scenes

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND

from annatar import config
from annatar.api.core import streams
from annatar.config import UserConfig
from annatar.debrid.models import StreamLink
from annatar.debrid.providers import DebridService, get_provider
from annatar.debrid.real_debrid_provider import RealDebridProvider
from annatar.stremio import StreamResponse

router = APIRouter()

log = structlog.get_logger(__name__)


FORWARD_ORIGIN_IP = os.environ.get("FORWARD_ORIGIN_IP", "false").lower() == "true"
OVERRIDE_ORIGIN_IP = os.environ.get("OVERRIDE_ORIGIN_IP", None)
ORIGIN_IP_HEADER = os.environ.get("ORIGIN_IP_HEADER") or "X-Forwarded-For"


class MediaType(str, Enum):
    movie = "movie"
    series = "series"
    porn = "porn"

    def __str__(self):
        return self.value

    @staticmethod
    def all() -> list[str]:
        return [media_type.value for media_type in MediaType]


@router.get("/")
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/configure", status_code=HTTP_302_FOUND)


@router.get("/manifest.json")
async def get_manifst_with_config(request: Request) -> dict[str, Any]:
    default_config = b64encode(UserConfig.defaults().model_dump_json().encode()).decode()
    return await get_manifest(
        request=request,
        b64config=default_config,
    )


def get_source_ip(request: Request) -> str:
    if OVERRIDE_ORIGIN_IP:
        return OVERRIDE_ORIGIN_IP

    source_ip = ""

    if request.client and FORWARD_ORIGIN_IP:
        source_ip = request.headers.get(
            ORIGIN_IP_HEADER,
            request.client.host,
        ).split(
            ","
        )[0]
    return source_ip


@router.get("/{b64config:str}/manifest.json")
async def get_manifest(request: Request, b64config: str) -> dict[str, Any]:
    user_config: UserConfig = config.parse_config(b64config)
    debrid: Optional[DebridService] = get_provider(
        provider_name=user_config.debrid_service,
        api_key=user_config.debrid_api_key,
        source_ip=get_source_ip(request),
    )
    app_name: str = config.APP_NAME
    if debrid:
        app_name = f"{app_name} {debrid.short_name()}"
    return {
        "id": config.APP_ID + debrid.short_name() if debrid else config.APP_ID,
        "icon": "https://i.imgur.com/p4V821B.png",
        "version": "0.0.1",
        "catalogs": generate_catalogs(),
        "idPrefixes": ["tt", "porn_"],
        "resources": [
            "stream",
            "catalog",
            {"name": "meta", "types": ["porn"], "idPrefixes": ["porn_"]},
        ],
        "types": MediaType.all(),
        "name": app_name,
        "logo": "https://i.imgur.com/p4V821B.png",
        "description": "Lord of Gifts. Search popular torrent sites and Debrid caches for streamable content.",
        "behaviorHints": {
            "adult": True,
            "configurable": True,
            "configurationRequired": False,
        },
    }


@router.get("/{b64config:str}/meta/{type:str}/{id:str}.json")
async def get_meta(request: Request, type: str, id: str) -> dict[str, Any]:
    if type == "porn":
        scene = await get_scene(id.split("_")[1])
        return {"meta": scene}


@router.get("/{b64config:str}/catalog/{type:str}/{id:str}.json")
@router.get("/{b64config:str}/catalog/{type:str}/{id:str}/skip={skip:int}.json")
@router.get("/{b64config:str}/catalog/{type:str}/{id:str}/genre={tag:str}.json")
@router.get("/{b64config:str}/catalog/{type:str}/{id:str}/genre={tag:str}&skip={skip:int}.json")
@router.get("/catalog/{type:str}/{id:str}.json")
async def get_catalog(
    request: Request,
    type: str,
    id: str,
    tag: Optional[str] = None,
    skip: Optional[int] = None,
) -> dict[str, Any]:
    if type == "porn":
        scenes = await get_scenes(tag=tag, sort=Sort.LATEST, skip=skip)
        return {"metas": scenes}


@router.get("/api/v2/hashes/{imdb_id:str}", description="Get hashes for a given IMDB ID")
async def get_hashes(
    imdb_id: Annotated[str, Path(description="IMDB ID", examples=["tt0120737"])],
    limit: Annotated[int, Query(description="Limit results", defualt=10)] = 10,
    season: Annotated[int | None, Query(description="Season", defualt=None)] = None,
    episode: Annotated[int | None, Query(description="Episode", defualt=None)] = None,
) -> dict[str, Any]:
    hashes = await streams.get_hashes(imdb_id=imdb_id, limit=limit, season=season, episode=episode)
    return {
        "hashes": hashes,
    }


@router.get(
    "/rd/{debrid_api_key:str}/{info_hash:str}/{file_id:int}",
    response_model=StreamResponse,
    response_model_exclude_none=True,
)
async def get_rd_stream(
    request: Request,
    debrid_api_key: Annotated[str, Path(description="Debrid token")],
    info_hash: Annotated[str, Path(description="Torrent info hash")],
    file_id: Annotated[int, Path(description="ID of the file in the torrent")],
) -> RedirectResponse:
    rd: RealDebridProvider = RealDebridProvider(
        api_key=debrid_api_key,
        source_ip=get_source_ip(request),
    )
    stream: Optional[StreamLink] = await rd.get_stream_for_torrent(
        info_hash=info_hash,
        file_id=file_id,
        debrid_token=debrid_api_key,
    )
    if not stream:
        raise HTTPException(status_code=404, detail="No stream found")

    return RedirectResponse(url=stream.url, status_code=HTTP_302_FOUND)


@router.get(
    "/{b64config:str}/stream/{type:str}/{id:str}.json",
    response_model=StreamResponse,
    response_model_exclude_none=True,
)
async def list_streams(
    request: Request,
    type: MediaType,
    id: Annotated[
        str,
        Path(
            title="stashDB ID",
            examples=["15a44ecc-01cc-4161-b5b1-814cf96ddf0c"],
            regex=r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        ),
    ],
    b64config: Annotated[str, Path(description="base64 encoded json blob")],
) -> StreamResponse:
    user_config: UserConfig = config.parse_config(b64config)
    debrid: Optional[DebridService] = get_provider(
        provider_name=user_config.debrid_service,
        api_key=user_config.debrid_api_key,
        source_ip=get_source_ip(request),
    )
    if not debrid:
        raise HTTPException(status_code=400, detail="Invalid debrid service")

    stashdb_id: str = id
    # season_episode: list[int] = [int(i) for i in id.split(":")[1:]]
    res: StreamResponse = await streams.search(
        type=type,
        debrid=debrid,
        stashdb_id=stashdb_id,
        # season_episode=season_episode,
        max_results=user_config.max_results,
        indexers=user_config.indexers,
        resolutions=user_config.resolutions,
    )

    for stream in res.streams:
        if stream.url.startswith("/"):
            stream.url = f"{request.url.scheme}://{request.url.netloc}{stream.url}"

    return res
