"""mheat-client — thin Python SDK for the MHEAT marine-heatwave HTTP API.

Quickstart::

    from mheat_client import MheatClient

    client = MheatClient("http://localhost:8000")
    print(client.health())
    events = client.events(start="2022-05-15", end="2022-09-15", min_category=3)
    cube = client.sst_cube()
"""

from .client import MheatClient

__all__ = ["MheatClient", "__version__"]
__version__ = "0.1.0"
