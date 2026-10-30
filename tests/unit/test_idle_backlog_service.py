import time

from src.services.idle_backlog_service import IdleBacklogService, Lane, SliceResult


def make_service(lanes, *, pressure=None, counts=None):
    recorded = []

    async def no_pressure(_limit):
        return None

    async def default_counts():
        return {lane.name: 5 for lane in lanes}

    async def record(cycle, _started):
        recorded.append(cycle)

    service = IdleBacklogService(
        lanes,
        pressure=pressure or no_pressure,
        counts=counts or default_counts,
        record=record,
    )
    return service, recorded


def scripted_lane(name, results, calls):
    queue = list(results)

    async def work(lane, slice_size, workers):
        calls.append(name)
        result = queue.pop(0)
        if not result.exhausted and not result.outage:
            lane.claims = object()
        return result

    return Lane(name, work)


async def test_foreground_pressure_skips_every_lane():
    calls = []
    lane = scripted_lane("articles", [SliceResult(finalised=3, exhausted=True)], calls)

    async def busy(_limit):
        return "release export is queued or running"

    service, recorded = make_service([lane], pressure=busy)
    cycle = await service.process_once()

    assert cycle["status"] == "busy"
    assert calls == []
    assert recorded and recorded[0]["busy_reason"] == "release export is queued or running"


async def test_empty_queue_rests_without_calling_models():
    calls = []
    lane = scripted_lane("articles", [], calls)

    async def empty():
        return {"articles": 0}

    service, _ = make_service([lane], counts=empty)
    cycle = await service.process_once()

    assert calls == []
    assert cycle["status"] == "idle"
    assert lane.next_at > time.monotonic() + 60


async def test_finished_pass_with_progress_returns_soon_and_without_rests_long():
    calls = []
    progressed = scripted_lane("articles", [SliceResult(finalised=5, exhausted=True)], calls)
    stuck = scripted_lane("summaries", [SliceResult(finalised=0, deferred=4, exhausted=True)], calls)
    service, _ = make_service([progressed, stuck])
    cycle = await service.process_once()

    assert cycle["status"] == "worked"
    assert calls == ["articles", "summaries"]
    soon = progressed.next_at - time.monotonic()
    long_rest = stuck.next_at - time.monotonic()
    assert soon <= 30
    assert long_rest > 600


async def test_unfinished_pass_keeps_claims_for_the_next_slice():
    calls = []
    lane = scripted_lane(
        "articles",
        [SliceResult(finalised=10), SliceResult(finalised=2, exhausted=True)],
        calls,
    )
    service, _ = make_service([lane])

    await service.process_once()
    claims = lane.claims
    assert claims is not None
    assert lane.pass_finalised == 10

    # The pass is still open, so the lane is not re-counted and keeps its claims.
    lane.next_at = 0.0
    await service.process_once()
    assert calls == ["articles", "articles"]
    assert lane.claims is None
    assert lane.pass_finalised == 0


async def test_outage_backs_off_and_stops_later_lanes():
    calls = []
    failing = scripted_lane("articles", [SliceResult(outage="route down")], calls)
    later = scripted_lane("summaries", [SliceResult(finalised=1, exhausted=True)], calls)
    service, _ = make_service([failing, later])
    cycle = await service.process_once()

    assert calls == ["articles"]
    assert cycle["lanes"]["articles"]["outage"] == "route down"
    assert failing.failures == 1
    assert failing.next_at > time.monotonic()


async def test_lane_exception_is_treated_as_an_outage():
    async def boom(lane, slice_size, workers):
        raise RuntimeError("database went away")

    lane = Lane("articles", boom)
    service, _ = make_service([lane])
    cycle = await service.process_once()

    assert "database went away" in cycle["lanes"]["articles"]["outage"]
    assert lane.failures == 1


async def test_new_foreground_work_stops_the_cycle_between_lanes():
    calls = []
    first = scripted_lane("articles", [SliceResult(finalised=2, exhausted=True)], calls)
    second = scripted_lane("summaries", [SliceResult(finalised=2, exhausted=True)], calls)
    answers = iter([None, "1 AI task(s) waiting for a worker"])

    async def pressure(_limit):
        return next(answers, "1 AI task(s) waiting for a worker")

    service, _ = make_service([first, second], pressure=pressure)
    cycle = await service.process_once()

    assert calls == ["articles"]
    assert cycle["status"] == "yielded"
