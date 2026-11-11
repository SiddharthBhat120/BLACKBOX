"""Four ingesters behind one interface. Each turns raw input into common Events."""
from .alerts import AlertIngester
from .base import Ingester
from .chat import ChatIngester
from .deploys import DeployIngester
from .logs import LogIngester

INGESTERS: dict[str, Ingester] = {
    "log": LogIngester(), "alert": AlertIngester(), "deploy": DeployIngester(), "chat": ChatIngester(),
}

__all__ = ["INGESTERS", "Ingester", "LogIngester", "AlertIngester", "DeployIngester", "ChatIngester"]
