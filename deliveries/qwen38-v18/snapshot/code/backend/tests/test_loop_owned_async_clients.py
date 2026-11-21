"""Loop-owned async clients must not leak across loops or close per subtask."""

from __future__ import annotations

import asyncio

from deerflow.models.factory import close_loop_async_clients, get_loop_async_client


def test_same_loop_tasks_share_client_until_loop_lifecycle_close():
    async def main():
        loop = asyncio.get_running_loop()
        first = get_loop_async_client(timeout=1.0, base_url="http://local")
        second = get_loop_async_client(timeout=1.0, base_url="http://local")
        assert first is not None and second is first
        assert not first.is_closed

        async def completed_subtask():
            # A completed subtask must not close a client still owned by the
            # loop; only the loop lifecycle close does that.
            return first

        await completed_subtask()
        assert not first.is_closed
        await close_loop_async_clients(loop)
        assert first.is_closed

    asyncio.run(main())


def test_finished_sibling_does_not_close_client_used_by_another_task():
    async def main():
        loop = asyncio.get_running_loop()
        client = get_loop_async_client(timeout=1.0, base_url="http://local")
        assert client is not None
        first_finished = asyncio.Event()

        async def short_task():
            first_finished.set()

        async def dependent_task():
            await first_finished.wait()
            assert client is not None and not client.is_closed

        await short_task()
        await dependent_task()
        await close_loop_async_clients(loop)
        assert client.is_closed

    asyncio.run(main())


def test_different_loops_get_different_clients():
    async def make_client():
        return get_loop_async_client(timeout=1.0, base_url="http://local")

    first = asyncio.run(make_client())
    second = asyncio.run(make_client())
    assert first is not None and second is not None
    assert first is not second
    assert not first.is_closed and not second.is_closed


def test_outside_running_loop_uses_normal_model_factory_path():
    assert get_loop_async_client(timeout=1.0, base_url="http://local") is None
