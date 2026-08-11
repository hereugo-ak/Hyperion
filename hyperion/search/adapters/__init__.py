"""Search adapters — one per provider, all search-only (title+url+snippet).

No adapter fetches page bodies and no adapter sends contents/answer/summarize
flags — extraction belongs to Hyperion's own ladder (§3a/§8/§12).
"""

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.adapters.exa import ExaAdapter
from hyperion.search.adapters.searxng import SearxNGAdapter
from hyperion.search.adapters.tavily import TavilyAdapter
from hyperion.search.adapters.yep import YepAdapter
from hyperion.search.adapters.you import YouAdapter

__all__ = [
    "BaseAdapter",
    "ExaAdapter",
    "SearxNGAdapter",
    "TavilyAdapter",
    "TransientProviderError",
    "YepAdapter",
    "YouAdapter",
]
