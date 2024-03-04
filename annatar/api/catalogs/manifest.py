from typing import Any
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport

TAGS = {
    "Anal": {
        "id": "b70c78b7-a25a-4c82-9929-591a5795b54d",
    },
    "Trans": {
        "id": "dc81ff52-bad0-4441-b20c-fd258b087d77",
    },
    "Lesbian": {"id": "9b506275-1883-48f4-bfee-d68d25a65ef2"},
    "Gay": {"id": "237e0f29-4db5-4d01-af7c-a620a73ed432"},
    "Teen": {"id": "6ddb272f-4701-4914-a07e-de917274d308"},
}


def get_tags() -> list[str]:
    return sorted(TAGS.keys())


def generate_catalogs() -> list[dict[str, Any]]:
    return [
        {
            "id": "top",
            "type": "porn",
            "name": "Trending",
            "extra": [
                {"name": "genre", "isRequired": False},
                {"name": "search", "isRequired": False},
            ],
            "genres": get_tags(),
        },
    ]
