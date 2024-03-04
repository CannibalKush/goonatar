from enum import Enum
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

    async def fetch_data(self, query, variables=None):
        gql_query = gql(query)
        result = await self.client.execute_async(gql_query, variable_values=variables)
        return result


ENDPOINT_URL = "https://stashdb.org/graphql"


class Sort(Enum):
    TRENDING = "TRENDING"
    LATEST = "CREATED_AT"


async def get_scenes(tag: str = None, sort: Sort = Sort.LATEST, skip: int = 0):
    tags_query_part = "tags: {value: [$tag], modifier: INCLUDES}, " if tag else ""
    tags_definition = "$tag: ID!," if tag else ""
    page = (skip // 50) + 1

    query = f"""
        query GetScenesByTag({tags_definition} $sort: SceneSortEnum!, $page: Int!) {{
        queryScenes(input: {{{tags_query_part}sort: $sort, direction: DESC, per_page: 50, page: $page}}) {{
            count
            scenes {{
            id
            title
            release_date
            tags {{
                name
            }}
            images {{
                url
            }}
            }}
        }}
        }}
    """
    log.info(query)
    variables = {"sort": sort.value, "page": page}
    if tag:
        variables["tag"] = TAGS[tag]["id"]
    result = await GQLClient(ENDPOINT_URL).fetch_data(query, variables)

    scenes_data = []
    log.info(result)
    for scene in result["queryScenes"]["scenes"]:
        scene_data = {
            "type": "porn",
            "id": scene["id"],
            "name": scene["title"],
            "poster": (scene["images"][0]["url"] if scene["images"] else None),
            "genres": [tag["name"] for tag in scene["tags"]],
        }
        scenes_data.append(scene_data)

    return scenes_data
