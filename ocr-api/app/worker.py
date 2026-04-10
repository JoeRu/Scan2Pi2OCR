import asyncio

_queue: asyncio.Queue | None = None
_status: dict = {}


def _get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue_job(job_id: str, tmp_dir: str) -> None:
    _status[job_id] = {"status": "queued"}
    await _get_queue().put((job_id, tmp_dir))


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def worker_loop() -> None:
    global _queue
    _queue = asyncio.Queue()  # fresh queue bound to current event loop
    try:
        while True:
            job_id, tmp_dir = await _queue.get()
            _status[job_id] = {"status": "processing"}
            _queue.task_done()
    except asyncio.CancelledError:
        pass
