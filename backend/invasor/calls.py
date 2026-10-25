"""Rules shared by ctx members: the ones that block (ctx.http, ctx.steam, ctx.archive, Task.ask),
and the URLs a module gives (ctx.http, ctx.steam.open_url)."""
import asyncio
import re
from urllib.parse import urlsplit

from .schema import InvalidArgument, Unavailable

MAX_URL = 2048
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def not_on_loop(what):
    """Unavailable on the event loop's thread: `what` blocks, and there it would freeze the service."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise Unavailable(f"{what} blocks: call it from a plain (threaded) method, or use its _async twin in `async def`")


def check(ctx, what):
    """not_on_loop, and Unavailable once the module's ctx is retired."""
    not_on_loop(what)
    if ctx._dead:
        raise Unavailable(f"this module was unloaded: {what} isn't available anymore")


def check_url(url, schemes):
    """`url` if it's a URL of one of `schemes` with a host, up to MAX_URL characters and no control
    characters; else InvalidArgument."""
    if not isinstance(url, str) or len(url) > MAX_URL or CONTROL.search(url):
        url_ok = False
    else:
        parts = urlsplit(url)
        url_ok = parts.scheme in schemes and bool(parts.hostname)
    if not url_ok:
        raise InvalidArgument(f"not a {'/'.join(schemes)} URL (up to {MAX_URL} characters): {str(url)[:100]!r}")
    return url
