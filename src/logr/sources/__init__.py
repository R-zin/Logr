from logr.models import Source
from logr.sources.base import Connector
from logr.sources.devfolio import DevfolioConnector
from logr.sources.devpost import DevpostConnector
from logr.sources.hack2skill import Hack2skillConnector
from logr.sources.luma import LumaConnector
from logr.sources.unstop import UnstopConnector

# LinkedIn is intentionally absent: it has no public listings API and its terms
# forbid scraping. It arrives in Phase 6 as user-submitted posts.
CONNECTORS: dict[Source, type[Connector]] = {
    Source.DEVPOST: DevpostConnector,
    Source.DEVFOLIO: DevfolioConnector,
    Source.UNSTOP: UnstopConnector,
    Source.HACK2SKILL: Hack2skillConnector,
    Source.LUMA: LumaConnector,
}

__all__ = ["CONNECTORS", "Connector"]
