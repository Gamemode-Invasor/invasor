"""Rules shared by the ctx members that block (ctx.http, ctx.steam, ctx.archive, Task.ask)."""
import asyncio

from .schema import Unavailable


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
