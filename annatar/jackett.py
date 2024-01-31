import asyncio
import re
from functools import lru_cache
from typing import Any, Optional

import aiohttp
import structlog
from structlog.contextvars import bound_contextvars

from annatar.debrid import magnet
from annatar.jackett_models import SearchQuery, SearchResult
from annatar.logging import timestamped
from annatar.torrent import Torrent

log = structlog.get_logger(__name__)
PRIORITY_WORDS: list[str] = [r"\b(4K|2160p)\b", r"\b1080p\b", r"\b720p\b"]
# TODO: need to gather these from jackett on startup or take this as an env var
# and then verify on startup. Jackett will accept incorrect values though and
# just return no results
ALL_INDEXERS: list[str] = [
    "yts",
    "eztv",
    "kickasstorrents-ws",
    "thepiratebay",
    "therarbg",
    "torrentgalaxy",
]


@timestamped
async def search_indexer(
    debrid_api_key: str,
    jackett_url: str,
    jackett_api_key: str,
    search_query: SearchQuery,
    max_results: int,
    indexer: str,
    imdb: int | None = None,
    timeout: int = 60,
) -> list[Torrent]:
    search_url: str = f"{jackett_url}/api/v2.0/indexers/all/results"
    category: str = "2000" if search_query.type == "movie" else "5000"
    suffix: str = (
        f"S{str(search_query.season).zfill(2)} E{str(search_query.episode).zfill(2)}"
        if search_query.type == "series"
        else search_query.year or ""
    )
    params: dict[str, Any] = {
        "apikey": jackett_api_key,
        "Category": category,
        "Query": f"{search_query.name} {suffix}".strip(),
    }
    params["Tracker[]"] = indexer

    async with aiohttp.ClientSession() as session:
        log.info(
            "searching jackett",
            query=search_query.model_dump(),
            params=params,
            indexer=indexer,
        )
        try:
            async with session.get(
                search_url,
                params=params,
                timeout=timeout,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    log.info(
                        "jacket search failed",
                        status=response.status,
                        reason=response.reason,
                        body=await response.text(),
                        indexer=indexer,
                    )
                    return []
                response_json = await response.json()
        except TimeoutError as err:
            log.error(
                "jacket search timeout",
                error=err,
                query=search_query.model_dump(),
                params=params,
                url=search_url,
                indexer=indexer,
            )
            return []

    search_results: list[SearchResult] = sorted(
        [SearchResult(**result) for result in response_json["Results"]],
        key=lambda r: r.Seeders,
        reverse=True,
    )

    torrents: dict[str, Torrent] = {}
    tasks = [
        asyncio.create_task(map_matched_result(result=result, imdb=imdb))
        for result in search_results
    ]

    for task in asyncio.as_completed(tasks):
        torrent: Optional[Torrent] = await task
        if torrent:
            torrents[torrent.info_hash] = torrent
            if len(torrents) >= max_results:
                break

    for task in tasks:
        if not task.done():
            task.cancel()

    # prioritize items by quality
    prioritized_list: list[Torrent] = sorted(
        list(torrents.values()),
        key=lambda t: sort_priority(search_query.name, t),
    )

    return prioritized_list


@timestamped
async def search_indexers(
    debrid_api_key: str,
    jackett_url: str,
    jackett_api_key: str,
    search_query: SearchQuery,
    max_results: int,
    imdb: int | None = None,
    timeout: int = 60,
    indexers: list[str] = ALL_INDEXERS,
) -> list[Torrent]:
    log.info("searching indexers", indexers=indexers)
    torrents: dict[str, Torrent] = {}
    tasks = [
        asyncio.create_task(
            search_indexer(
                debrid_api_key=debrid_api_key,
                jackett_url=jackett_url,
                jackett_api_key=jackett_api_key,
                search_query=search_query,
                max_results=max_results,
                imdb=imdb,
                timeout=timeout / 3,
                indexer=indexer,
            )
        )
        for indexer in indexers
    ]
    for task in asyncio.as_completed(tasks):
        indexer_results: list[Optional[Torrent]] = await task
        for torrent in indexer_results:
            if torrent:
                torrents[torrent.info_hash] = torrent
                if len(torrents) >= max_results:
                    break

    for task in tasks:
        if not task.done():
            task.cancel()

    return list(torrents.values())


async def map_matched_result(result: SearchResult, imdb: int | None) -> Torrent | None:
    if imdb and result.Imdb and result.Imdb != imdb:
        log.info(
            "skipping mismatched IMDB",
            wanted=imdb,
            result=result.model_dump(),
        )
        return None

    if result.InfoHash and result.MagnetUri:
        return Torrent(
            guid=result.Guid,
            info_hash=result.InfoHash,
            title=result.Title,
            size=result.Size,
            url=result.MagnetUri,
            seeders=result.Seeders,
        )

    if result.Link and result.Link.startswith("http"):
        magnet_link: str | None = await resolve_magnet_link(guid=result.Guid, link=result.Link)
        if not magnet_link:
            return None

        info_hash: str | None = result.InfoHash or magnet.get_info_hash(magnet_link)
        if not info_hash:
            log.info("Could not find info hash in magnet link", magnet=magnet_link)
            return None

        torrent: Torrent = Torrent(
            guid=result.Guid,
            info_hash=info_hash,
            title=result.Title,
            size=result.Size,
            url=magnet_link,
            seeders=result.Seeders,
        )
        log.info("found torrent", torrent=torrent.model_dump())
        return torrent

    return None


# sort items by quality
def sort_priority(search_query: str, item: Torrent) -> int:
    name_pattern: str = re.sub(r"\W+", r"\\W+", search_query)
    with bound_contextvars(
        search_query=search_query,
        name_pattern=name_pattern,
        torrent=item.info_hash,
    ):
        log.debug("set torrent priority")

        priority: int = 50
        reason: str = "no priority matches"
        for index, quality in enumerate(PRIORITY_WORDS):
            if re.search(quality, item.title, re.IGNORECASE):
                if re.search(name_pattern, item.title, re.IGNORECASE):
                    priority = index
                    reason = f"matched {quality} and name"
                else:
                    priority = index + 10
                    reason = f"matched {quality}, mismatch name"
        log.debug("torrent priority set", priority=priority, reason=reason)
        return priority


@lru_cache(maxsize=1024, typed=True)
async def resolve_magnet_link(guid: str, link: str) -> str | None:
    """
    Jackett sometimes does not have a magnet link but a local URL that
    redirects to a magnet link. This will not work if adding to RD and
    Jackett is not publicly hosted. Most of the time we can resolve it
    locally. If not we will just pass it along to RD anyway
    """
    if link.startswith("magnet"):
        return link

    log.info("magnet resolve: following redirect", guid=guid, link=link)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(link, allow_redirects=False, timeout=1) as response:
                if response.status == 302:
                    location = response.headers.get("Location", "")
                    log.info("magnet resolve: found redirect", guid=guid, magnet=location)
                    return location
                else:
                    log.info("magnet resolve: no redirect found", guid=guid, status=response.status)
                    return None
    except TimeoutError as err:
        log.error("magnet resolve: timeout", guid=guid, error=err)
        return None