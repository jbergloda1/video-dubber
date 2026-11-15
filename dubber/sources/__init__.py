"""Source adapters registry. To add a platform: subclass base.SourceAdapter and register it here."""
from .douyin import Douyin
from .facebook import FacebookReels

REGISTRY = {a.id: a for a in (Douyin(), FacebookReels())}
