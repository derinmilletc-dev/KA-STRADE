import asyncio
from types import SimpleNamespace

from app import snapshot_coalesce


def test_burst_writes_collapse_and_last_wins():
    app = SimpleNamespace(state=SimpleNamespace())
    written = []

    async def writer(_app, key, payload):
        await asyncio.sleep(0.05)
        written.append((key, payload))

    async def main():
        tasks = [snapshot_coalesce.queue(app, "t", "u1", i, writer) for i in range(20)]
        await asyncio.gather(*tasks)
        return tasks

    tasks = asyncio.run(main())
    assert len({id(t) for t in tasks}) == 1
    assert written[-1] == ("u1", 19)
    assert len(written) <= 2


def test_new_write_after_finish_runs_and_errors_reported():
    app = SimpleNamespace(state=SimpleNamespace())
    seen, errors = [], []

    async def writer(_app, key, payload):
        if payload == "bad":
            raise RuntimeError("x")
        seen.append(payload)

    async def main():
        await snapshot_coalesce.queue(app, "t", "k", "a", writer, errors.append)
        await snapshot_coalesce.queue(app, "t", "k", "bad", writer, errors.append)
        await snapshot_coalesce.queue(app, "t", "k", "b", writer, errors.append)

    asyncio.run(main())
    assert seen == ["a", "b"] and len(errors) == 1
