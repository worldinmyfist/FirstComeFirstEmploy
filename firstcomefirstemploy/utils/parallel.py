"""Run independent, I/O-bound calls concurrently."""

from concurrent.futures import ThreadPoolExecutor

from ..config import DESCRIPTION_WORKERS


def map_concurrent(func, items, workers=None):
    """Return [func(item) for item in items], using a thread pool.

    Result order matches `items`. The first exception raised by `func` is
    re-raised and calls that have not started yet are cancelled, so a strict
    failure (e.g. a description the run cannot do without) stops quickly.
    """
    items = list(items)

    if not items:
        return []

    workers = max(1, min(workers or DESCRIPTION_WORKERS, len(items)))

    if workers == 1:
        return [func(item) for item in items]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(func, item) for item in items]

        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise