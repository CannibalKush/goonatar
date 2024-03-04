from enum import Enum
from typing import Any
from annatar.api.catalogs.manifest import TAGS
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport

import structlog

log = structlog.get_logger(__name__)


class GQLClient:
    _instance = None

    def __new__(cls, endpoint_url):
        if cls._instance is None:
            cls._instance = super(GQLClient, cls).__new__(cls)
            # Initialize the client only once
            transport = AIOHTTPTransport(
                url=endpoint_url,
                headers={
                    "apiKey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiJkYTEwZmM4Zi1jNzdmLTQ4NWEtOTY3Ni03OWQzYTFkY2UxNzQiLCJzdWIiOiJBUElLZXkiLCJpYXQiOjE3MDkyMDAzMzl9.LAMSj4S2TtbWNqfiglY0D97o7AXEiyPUrMW9yP4EsRo"
                },
            )
            cls._instance.client = Client(
                transport=transport,
                fetch_schema_from_transport=True,
            )
        return cls._instance

    @classmethod
    async def fetch_data(cls, query, variables=None):
        # Ensure the client is initialized
        if cls._instance is None:
            cls(ENDPOINT_URL)  # Initialize with your endpoint URL
        return await cls._instance._fetch_data(query, variables)

    async def _fetch_data(self, query, variables=None):
        gql_query = gql(query)
        try:
            result = await self.client.execute_async(gql_query, variable_values=variables)
            return result
        except Exception as e:
            log.error("GraphQL query failed", error=str(e), query=query)
            return None


ENDPOINT_URL = "https://stashdb.org/graphql"


class Sort(Enum):
    TRENDING = "TRENDING"
    LATEST = "CREATED_AT"


def construct_scene_fields():
    return """
        id
        title
        details
        release_date
        tags {
            name
        }
        urls {
            url
        }
        performers {
            performer {
                name
            }
        }
        studio {
            images {
                url
            }
        }
        images {
            url
        }
        duration
        director
    """


async def get_scenes(tag: str = None, sort: Sort = Sort.LATEST, skip: int = 0):
    skip = int(skip) if skip is not None else 0
    tags_query_part = "tags: {value: [$tag], modifier: INCLUDES}, " if tag else ""
    tags_definition = "$tag: ID!," if tag else ""
    page = skip // 50 + 1
    scene_fields = construct_scene_fields()

    query = f"""
        query GetScenesByTag({tags_definition} $sort: SceneSortEnum!, $page: Int!) {{
        queryScenes(input: {{{tags_query_part}sort: $sort, direction: DESC, per_page: 50, page: $page}}) {{
            count
            scenes {{
                {scene_fields}
            }}
        }}
        }}
    """
    log.info(query)
    variables = {"sort": sort.value, "page": page}
    if tag:
        variables["tag"] = TAGS[tag]["id"]
    result = await GQLClient.fetch_data(query, variables)

    scenes_data = []
    for scene in result["queryScenes"]["scenes"]:
        scene_data = parse_scene_data(scene, True)
        scenes_data.append(scene_data)

    return scenes_data


async def get_scene(id: str) -> dict[str, Any]:
    scene_fields = construct_scene_fields()
    query = f"""
    query GetScene($id: ID!) {{
        findScene(id: $id) {{
            {scene_fields}
        }}
    }}
    """
    variables = {"id": id}

    result = await GQLClient.fetch_data(query, variables)
    scene = result.get("findScene")
    if not scene:
        return {}
    return parse_scene_data(scene, False)


def parse_scene_data(scene: dict, logo: bool) -> dict[str, Any]:
    poster = scene["images"][0]["url"] if scene["images"] else None
    duration_str = format_duration(scene["duration"])
    name = str(scene.get("title", ""))
    description = str(scene.get("details", ""))
    studio_logo = get_studio_logo(scene) if logo else None

    scene_data = {
        "id": f"porn_{scene['id']}",
        "type": "porn",
        "name": name,
        "poster": poster,
        "genres": [tag["name"] for tag in scene.get("tags", [])],
        "cast": [performer["performer"]["name"] for performer in scene.get("performers", [])],
        "director": scene.get("director", None),
        "website": scene.get("urls", [{}])[0].get("url", None),
        "description": name + "\n\n" + description if studio_logo else description,
        "background": get_background_image(scene, poster),
        "duration": duration_str,
        "released": scene["release_date"],
        "logo": studio_logo,
    }

    log.info(scene_data)
    return scene_data


def format_duration(duration_seconds: int) -> str:
    if duration_seconds < 180:
        return f"{duration_seconds}s"
    duration_minutes = round(duration_seconds / 60)
    return f"{duration_minutes}m"


def get_studio_logo(scene: dict) -> str:
    if scene["studio"] and scene["studio"]["images"]:
        return scene["studio"]["images"][0]["url"]
    return None


def get_background_image(scene: dict, poster: str) -> str:
    if scene["images"] and len(scene["images"]) > 1:
        return scene["images"][1]["url"]
    return poster
