"""Retained inference executions run to completion and stay retrievable after disconnect."""
import asyncio
import unittest
from unittest.mock import patch

from fastapi.responses import JSONResponse
from starlette.requests import Request

from omni.gateway import executions
from omni.gateway.proxy import Proxy


class FakeUpstream:
    """An upstream response yielding canned chunks, then closing."""

    def __init__(self, chunks, status=200, content_type="text/event-stream"):
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.closed = False

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk
            await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


class Ids(unittest.TestCase):
    def test_valid_ids_pass_and_garbage_does_not(self):
        self.assertEqual(executions.valid_id("abc-123_XY.9"), "abc-123_XY.9")
        for bad in (None, "", 123, "a" * 129, "has space", "semi;colon", "ünïcode"):
            self.assertIsNone(executions.valid_id(bad))


class Retention(unittest.TestCase):
    def setUp(self):
        executions.TASKS.clear()
        executions.ACTIVE.clear()
        executions.DONE.clear()
        self.addCleanup(executions.TASKS.clear)
        self.addCleanup(executions.ACTIVE.clear)
        self.addCleanup(executions.DONE.clear)

    def test_a_streamed_response_is_stored_whole(self):
        async def run():
            upstream = FakeUpstream([b"data: one\n\n", b"data: two\n\n"])
            sink = executions.begin("e1", "chat", "/v1/chat/completions", 200,
                                    "text/event-stream", upstream)
            live = [chunk async for chunk in sink.live_chunks()]
            entry = executions.get("e1")
            while entry is not None and entry["status"] == "running":
                await asyncio.sleep(0)
                entry = executions.get("e1")
            return live, entry

        live, entry = asyncio.run(run())
        self.assertEqual(b"".join(live), b"data: one\n\ndata: two\n\n")
        entry = executions.get("e1")
        self.assertEqual(entry["status"], "done")
        self.assertEqual(entry["body"], b"data: one\n\ndata: two\n\n")
        self.assertIsNone(entry["error"])
        self.assertFalse(entry["truncated"])

    async def settled(self, exec_id):
        entry = executions.get(exec_id)
        while entry is not None and entry["status"] == "running":
            await asyncio.sleep(0)
            entry = executions.get(exec_id)
        return entry

    def test_a_gone_client_never_stops_the_pump(self):
        async def run():
            upstream = FakeUpstream([b"one", b"two", b"three"])
            sink = executions.begin("e2", "chat", "/v1/chat/completions", 200,
                                    "text/event-stream", upstream)
            stream = sink.live_chunks()
            self.assertEqual(await stream.__anext__(), b"one")
            await stream.aclose()  # the browser closed mid-stream
            sink.detach()
            await self.settled("e2")
            return upstream

        upstream = asyncio.run(run())
        self.assertTrue(upstream.closed)
        entry = executions.get("e2")
        self.assertEqual(entry["body"], b"onetwothree")

    def test_bodies_past_the_cap_are_marked_truncated(self):
        async def run():
            upstream = FakeUpstream([b"xxxxx"])
            executions.begin("e3", "image", "/v1/images/generations", 200,
                             "application/json", upstream)
            await self.settled("e3")

        with patch.object(executions, "MAX_BODY_BYTES", 4):
            asyncio.run(run())
        entry = executions.get("e3")
        self.assertTrue(entry["truncated"])
        self.assertEqual(entry["body"], b"xxxx")
        self.assertEqual(entry["size"], 5)

    def test_a_direct_call_survives_a_cancelled_wait(self):
        async def answer():
            await asyncio.sleep(0.01)
            return JSONResponse({"ok": True})

        async def run():
            task = asyncio.create_task(executions.run("e4", "decide", "/v1/systemone", answer))
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            entry = executions.get("e4")
            while entry["status"] == "running":
                await asyncio.sleep(0.01)
                entry = executions.get("e4")
            return entry

        entry = asyncio.run(run())
        self.assertEqual(entry["status_code"], 200)
        self.assertIn(b'"ok":true', entry["body"])

    def test_a_failed_direct_call_is_replayed_as_its_error(self):
        async def answer():
            return JSONResponse({"error": {"message": "bad", "type": "x"}}, status_code=400)

        entry = asyncio.run(executions.run("e5", "decide", "/v1/systemone", answer))
        response = executions.http_response(entry)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"bad", response.body)

    def test_old_entries_age_out_of_recent(self):
        executions.DONE["old"] = {"id": "old", "role": "chat", "path": "/", "started_at": 0,
                                  "finished_at": 1.0, "status_code": 200, "content_type": "text/plain",
                                  "size": 1, "truncated": False, "body": b"x", "error": None}
        self.assertEqual(executions.recent(), [])
        self.assertIsNone(executions.get("old"))


class FakeClient:
    """An httpx stand-in answering every request with canned upstream chunks."""

    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status = status
        self.sends = 0
        self.last = None

    def build_request(self, method, path, content=None, headers=None):
        self.last = (method, path, content, headers)
        return (method, path)

    async def send(self, _request, stream=True):
        self.sends += 1
        return FakeUpstream(self._chunks, status=self.status,
                            content_type="text/event-stream")


def post_request(exec_id=None):
    headers = [(b"content-type", b"application/json")]
    if exec_id is not None:
        headers.append((b"x-omni-exec", exec_id.encode()))
    return Request({"type": "http", "method": "POST", "headers": headers})


class RetainedForward(unittest.TestCase):
    def setUp(self):
        executions.TASKS.clear()
        executions.ACTIVE.clear()
        executions.DONE.clear()
        self.addCleanup(executions.TASKS.clear)
        self.addCleanup(executions.ACTIVE.clear)
        self.addCleanup(executions.DONE.clear)

    async def settled(self, exec_id):
        entry = executions.get(exec_id)
        while entry is not None and entry["status"] == "running":
            await asyncio.sleep(0)
            entry = executions.get(exec_id)
        return entry

    def test_a_retained_stream_matches_the_live_bytes(self):
        async def run():
            proxy = Proxy({"chat": FakeClient([b"a", b"b"])}, {})
            response = await proxy.forward(post_request("r1"), "chat", "/v1/chat/completions",
                                           body=b"{}")
            self.assertEqual(response.status_code, 200)
            live = [chunk async for chunk in response.body_iterator]
            await self.settled("r1")
            return live

        self.assertEqual(b"".join(asyncio.run(run())), b"ab")
        self.assertEqual(executions.get("r1")["body"], b"ab")

    def test_a_disconnect_still_leaves_the_full_result(self):
        async def run():
            proxy = Proxy({"chat": FakeClient([b"a", b"b", b"c"])}, {})
            response = await proxy.forward(post_request("r2"), "chat", "/v1/chat/completions",
                                           body=b"{}")
            stream = response.body_iterator
            self.assertEqual(await stream.__anext__(), b"a")
            await stream.aclose()  # the browser closed mid-stream
            await self.settled("r2")

        asyncio.run(run())
        entry = executions.get("r2")
        self.assertEqual(entry["body"], b"abc")

    def test_a_finished_id_replays_without_touching_the_model(self):
        async def run():
            client = FakeClient([b"ab"])
            proxy = Proxy({"chat": client}, {})
            first = await proxy.forward(post_request("r3"), "chat", "/v1/chat/completions",
                                        body=b"{}")
            async for _chunk in first.body_iterator:
                pass
            await self.settled("r3")
            second = await proxy.forward(post_request("r3"), "chat", "/v1/chat/completions",
                                         body=b"{}")
            return client.sends, second.body

        sends, replayed = asyncio.run(run())
        self.assertEqual(sends, 1)
        self.assertEqual(replayed, b"ab")

    def test_a_running_id_is_a_conflict_pointing_at_its_status(self):
        async def run():
            release = asyncio.Event()

            class Slow(FakeUpstream):
                async def aiter_raw(self):
                    release.set()
                    await asyncio.sleep(60)
                    yield b"late"

            class SlowClient(FakeClient):
                async def send(self, _request, stream=True):
                    self.sends += 1
                    return Slow([b"x"])

            proxy = Proxy({"chat": SlowClient([b"x"])}, {})
            first = await proxy.forward(post_request("r4"), "chat", "/v1/chat/completions",
                                        body=b"{}")
            await release.wait()
            second = await proxy.forward(post_request("r4"), "chat", "/v1/chat/completions",
                                         body=b"{}")
            for task in list(executions.TASKS.values()):
                task.cancel()
            return first, second

        first, second = asyncio.run(run())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertIn(b"/admin/requests/r4", second.body)


    def test_stop_cancels_the_pump_and_closes_the_upstream(self):
        async def run():
            release = asyncio.Event()
            upstreams = []

            class Endless(FakeUpstream):
                async def aiter_raw(self):
                    yield b"first"
                    release.set()
                    await asyncio.sleep(60)
                    yield b"never"

            class EndlessClient(FakeClient):
                async def send(self, _request, stream=True):
                    upstreams.append(Endless([]))
                    return upstreams[-1]

            proxy = Proxy({"chat": EndlessClient([])}, {})
            await proxy.forward(post_request("r5"), "chat", "/v1/chat/completions", body=b"{}")
            await release.wait()
            self.assertTrue(executions.cancel("r5"))
            entry = await self.settled("r5")
            return entry, upstreams[0]

        entry, upstream = asyncio.run(run())
        self.assertEqual(entry["error"], executions.CANCELLED)
        self.assertEqual(entry["body"], b"first")
        self.assertTrue(upstream.closed)
        self.assertFalse(executions.cancel("r5"))

    def test_an_id_runs_and_conflicts_while_waiting_for_headers(self):
        async def run():
            release = asyncio.Event()

            class HeadersLater(FakeClient):
                async def send(self, _request, stream=True):
                    self.sends += 1
                    await release.wait()
                    return FakeUpstream([b"png"], content_type="application/json")

            client = HeadersLater([])
            proxy = Proxy({"image": client}, {})
            first = asyncio.create_task(proxy.forward(post_request("r6"), "image",
                                                      "/v1/images/generations", body=b"{}"))
            await asyncio.sleep(0)
            status = executions.get("r6")["status"]
            second = await proxy.forward(post_request("r6"), "image", "/v1/images/generations",
                                         body=b"{}")
            release.set()
            response = await first
            async for _chunk in response.body_iterator:
                pass
            await self.settled("r6")
            return status, second, client.sends

        status, second, sends = asyncio.run(run())
        self.assertEqual(status, "running")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(sends, 1)
        self.assertEqual(executions.get("r6")["body"], b"png")

    def test_a_failed_id_runs_again_instead_of_replaying_the_failure(self):
        async def run():
            client = FakeClient([b'{"error": "warming up"}'], status=503)
            proxy = Proxy({"chat": client}, {})
            failed = await proxy.forward(post_request("r7"), "chat", "/v1/chat/completions",
                                         body=b"{}")
            client.status = 200
            client._chunks = [b"ok"]
            retried = await proxy.forward(post_request("r7"), "chat", "/v1/chat/completions",
                                          body=b"{}")
            live = [chunk async for chunk in retried.body_iterator]
            await self.settled("r7")
            return failed.status_code, live, client.sends

        status, live, sends = asyncio.run(run())
        self.assertEqual(status, 503)
        self.assertEqual(b"".join(live), b"ok")
        self.assertEqual(sends, 2)

    def test_stop_while_waiting_for_headers_answers_cancelled(self):
        async def run():
            class Never(FakeClient):
                async def send(self, _request, stream=True):
                    await asyncio.sleep(60)

            proxy = Proxy({"image": Never([])}, {})
            pending = asyncio.create_task(proxy.forward(post_request("r8"), "image",
                                                        "/v1/images/generations", body=b"{}"))
            await asyncio.sleep(0)
            self.assertTrue(executions.cancel("r8"))
            return await pending

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 499)
        self.assertEqual(executions.get("r8")["error"], executions.CANCELLED)

if __name__ == "__main__":
    unittest.main()
