import asyncio
import os
import logging
import sys
import xml.etree.ElementTree as ET

import httpx

from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Dict, List
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

# https://www.youtube.com/feeds/videos.xml?channel_id=UCtinbF-Q-fVthA0qrFQTgXQ


class Cache(BaseModel):
    ttl: datetime
    value: str


class Author(BaseModel):
    name: str
    uri: str

    @classmethod
    def from_xml(cls, d: ET.Element):
        author = d.find("a:author", namespaces)
        return cls(
            name=author.find("a:name", namespaces).text,
            uri=author.find("a:uri", namespaces).text,
        )


class Media(BaseModel):
    title: str
    url: str
    thumbnail: str
    description: str | None
    avg_rating: float
    views: int

    @classmethod
    def from_xml(cls, d: ET.Element):
        try:
            media_group = d.find("m:group", namespaces)
            media_community = media_group.find("m:community", namespaces)

            return cls(
                title=media_group.find("m:title", namespaces).text,
                url=media_group.find("m:content", namespaces).get("url"),
                thumbnail=media_group.find("m:thumbnail", namespaces).get("url"),
                description=media_group.find("m:description", namespaces).text,
                avg_rating=float(
                    media_community.find("m:starRating", namespaces).get("average")
                ),
                views=int(
                    float(media_community.find("m:statistics", namespaces).get("views"))
                ),
            )
        except Exception as e:
            return None


class Video(BaseModel):
    id: str
    video_id: str
    channel_id: str
    title: str
    link: str
    author: Author
    published: datetime  # 2026-05-11T17:25:50+00:00
    updated: datetime  # 2026-05-12T07:44:44+00:00
    media: Media
    is_short: bool

    @classmethod
    def from_xml(cls, d: ET.Element):
        published = datetime.strptime(
            d.find("a:published", namespaces).text, "%Y-%m-%dT%H:%M:%S%z"
        )

        updated = datetime.strptime(
            d.find("a:updated", namespaces).text, "%Y-%m-%dT%H:%M:%S%z"
        )

        link: str = d.find("a:link", namespaces).get("href")
        is_short: bool = "/shorts/" in link

        return cls(
            id=d.find("a:id", namespaces).text,
            video_id=d.find("yt:videoId", namespaces).text,
            channel_id=d.find("yt:channelId", namespaces).text,
            title=d.find("a:title", namespaces).text,
            link=link,
            author=Author.from_xml(d),
            published=published,
            updated=updated,
            media=Media.from_xml(d),
            is_short=is_short,
        )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["*"] for dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

namespaces = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "m": "http://search.yahoo.com/mrss/",
}

channel_videos_cache: Dict[str, Cache] = {}


@app.get("/")
def root():
    return {"status": "alive"}


@app.get("/feed")
async def get_feed() -> List[Video]:
    try:
        subscriptions = get_subscriptions()
        feed: List[Video] = []
        results = await asyncio.gather(*(get_videos(i) for i in subscriptions))
        for result in results:
            feed.extend(result)
        return sorted(
            feed, key=lambda v: (-v.published.timestamp(), v.author.name, v.video_id)
        )
    except Exception as e:
        logger.info(e)
        return []


async def get_videos(id: str, q: str | None = None) -> List[Video]:
    try:
        logger.info(f"Getting {id}")
        result_sor = "CACHE"
        if (
            id not in channel_videos_cache
            or datetime.now() >= channel_videos_cache[id].ttl
        ):
            result_sor = "HTTP"
            logger.info(f"Sending HTTP request {id}")
            response = httpx.get(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={id}"
            )

            if response.status_code != 200:
                raise Exception(f"{response.status_code} - Unsuccessful request.")

            channel_videos_cache[id] = Cache(
                ttl=datetime.now() + timedelta(hours=24), value=response.text
            )

        logger.info(f"Data sourced from {result_sor}")
        data = channel_videos_cache[id].value
        root = ET.fromstring(data)
        entries = root.findall("a:entry", namespaces)
        videos: List[Video] = []
        for entry in entries:
            video = Video.from_xml(entry)
            if not video.is_short:
                videos.append(video)
        return videos
    except Exception as e:
        logger.info(e)
        return []


def get_subscriptions() -> List[str]:  # return a list of channel IDs
    subscriptions_csv = os.getenv("subscriptions")
    subscriptions = subscriptions_csv.split(",")
    return subscriptions
