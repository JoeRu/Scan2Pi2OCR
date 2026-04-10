import asyncio

_queue: asyncio.Queue = asyncio.Queue()
_status: dict = {}


async def enqueue_job(job_id: str, tmp_dir: str) -> None:
    _status[job_id] = {"status": "queued"}
    await _queue.put((job_id, tmp_dir))


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def worker_loop() -> None:
    while True:
        job_id, tmp_dir = await _queue.get()
        _status[job_id] = {"status": "processing"}
        _queue.task_done()
