import asyncio
import unittest

from dalicontrol_e64 import Macro, Writer, commands


class Device:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.gate = None

    async def send(self, command):
        commands.require_operational(command)
        self.calls.append(command)
        if self.gate:
            await self.gate.wait()
        await asyncio.sleep(0)
        if self.fail and command == self.fail:
            raise RuntimeError("timeout")


class WriterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = Device()
        self.writer = Writer(self.device.send)

    async def asyncTearDown(self):
        await self.writer.close()

    async def test_plain_request(self):
        job = self.writer.submit({1: 255, 2: 0})
        result = await job.future
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.device.calls, ["0B00FF", "0B0100"])

    async def test_newer_request_replaces_waiting_targets(self):
        self.device.gate = asyncio.Event()
        old = self.writer.submit({1: 10, 2: 10, 3: 10})
        await asyncio.sleep(0)  # first command of `old` is on the wire
        new = self.writer.submit({2: 200, 3: 200})
        self.device.gate.set()
        results = await asyncio.gather(old.future, new.future)
        self.assertEqual(results[0]["status"], "superseded")
        self.assertEqual(results[0]["commands"], ["0B000A"])
        self.assertEqual(results[1]["status"], "completed")
        self.assertEqual(self.device.calls, ["0B000A", "0B01C8", "0B02C8"])

    async def test_broadcast_stays_one_command(self):
        everything = dict.fromkeys(range(1, 65), 0)
        job = self.writer.submit(everything, macro=Macro(commands.ALL_OFF, dict(everything)))
        await job.future
        self.assertEqual(self.device.calls, ["0A0000"])

    async def test_failure_aborts_queue_without_retry(self):
        self.device.fail = "0B0001"
        first = self.writer.submit({1: 1, 2: 2})
        second = self.writer.submit({9: 9})
        results = await asyncio.gather(first.future, second.future)
        self.assertEqual([r["status"] for r in results], ["failed", "aborted"])
        self.assertEqual(self.device.calls.count("0B0001"), 1)
        again = self.writer.submit({9: 9})  # a fresh explicit request may send again
        self.assertEqual((await again.future)["status"], "completed")

    async def test_validation(self):
        for bad in ({}, {0: 1}, {65: 1}, {1: 256}):
            self.assertRaises(ValueError, self.writer.submit, bad)


if __name__ == "__main__":
    unittest.main()
