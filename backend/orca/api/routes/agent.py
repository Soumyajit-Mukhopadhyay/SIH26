"""``POST /agent/stream`` — the agent trace, streamed as it happens.

Why streaming is a correctness concern and not a nicety: an agent trace that
arrives in one lump at the end is indistinguishable from a fabricated one. The
whole explainability claim rests on a judge watching the plan appear, then the
tool calls land one at a time, then the critic rule, then the answer. So this
route is careful about the things that break incremental delivery:

* ``X-Accel-Buffering: no`` and ``Cache-Control: no-transform``, because nginx
  and most PaaS proxies buffer by default;
* no gzip on this path, since a compressor will happily sit on small frames;
* a heartbeat comment every few seconds, so an idle proxy does not reap the
  connection during a slow upstream fetch.

The event schema is ours, not LangGraph's, so a framework upgrade cannot silently
change the contract the frontend depends on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from orca.agents import llm
from orca.agents.graph import run as run_graph
from orca.agents.tools import catalogue
from orca.provenance import Lat, Lon, utcnow

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

#: Runs kept in memory for replay. Small and bounded: the trace store proper is
#: the agent_runs/agent_steps tables, and this is the read-through for the UI.
_RUNS: dict[str, dict[str, object]] = {}
_MAX_RUNS = 60

HEARTBEAT_S = 8.0


class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1200)
    lat: Lat
    lon: Lon
    loa_m: float = Field(default=8.2, gt=0, le=200)
    place: str | None = None
    locale: str = "en"
    thread_id: str | None = Field(
        default=None, description="Reuse to continue a conversation across a reload."
    )


def _sse(event: dict[str, object]) -> str:
    """One SSE frame. The event type goes in the `event:` field as well as the
    payload, so a client can use addEventListener or read `type` — whichever
    suits it — without us maintaining two schemas."""
    kind = str(event.get("type", "message"))
    data = json.dumps(event, default=str, ensure_ascii=False)
    return f"event: {kind}\ndata: {data}\n\n"


@router.post("/agent/stream", summary="Ask ORCA; receive the agent trace as SSE")
async def agent_stream(request: AgentRequest) -> StreamingResponse:
    run_id = uuid.uuid4().hex[:12]
    thread_id = request.thread_id or run_id

    async def generate() -> AsyncIterator[str]:
        collected: list[dict[str, object]] = []
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

        async def pump() -> None:
            """Drive the graph into a queue so the heartbeat can interleave.

            Without this, a 6-second upstream fetch means 6 seconds of total
            silence on the wire, which is exactly when a proxy decides the
            connection is dead.
            """
            try:
                async for event in run_graph(
                    request.question,
                    lat=request.lat,
                    lon=request.lon,
                    loa_m=request.loa_m,
                    place=request.place,
                    locale=request.locale,
                ):
                    await queue.put(event)
            except Exception as exc:
                log.exception("agent run %s failed", run_id)
                await queue.put(
                    {
                        "type": "error",
                        "at": utcnow().isoformat(),
                        "message": f"{type(exc).__name__}: {exc}",
                        "detail": (
                            "The agent run failed. The deterministic endpoints "
                            "(/risk/assess, /forecast/point) are unaffected."
                        ),
                    }
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())

        yield _sse(
            {
                "type": "run_started",
                "at": utcnow().isoformat(),
                "run_id": run_id,
                "thread_id": thread_id,
                "question": request.question,
                "tools_available": [t["name"] for t in catalogue()],
            }
        )

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    # An SSE comment: keeps the connection warm, ignored by clients.
                    yield ": heartbeat\n\n"
                    continue

                if event is None:
                    break

                event["run_id"] = run_id
                collected.append(event)
                yield _sse(event)
        finally:
            task.cancel()
            _remember(run_id, thread_id, request, collected)

        yield _sse({"type": "run_finished", "at": utcnow().isoformat(), "run_id": run_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Orca-Run-Id": run_id,
        },
    )


def _remember(
    run_id: str,
    thread_id: str,
    request: AgentRequest,
    events: list[dict[str, object]],
) -> None:
    if len(_RUNS) >= _MAX_RUNS:
        _RUNS.pop(next(iter(_RUNS)))
    _RUNS[run_id] = {
        "run_id": run_id,
        "thread_id": thread_id,
        "question": request.question,
        "lat": request.lat,
        "lon": request.lon,
        "loa_m": request.loa_m,
        "started_at": events[0].get("at") if events else None,
        "finished_at": utcnow().isoformat(),
        "events": events,
    }


@router.get("/agent/runs/{run_id}", summary="Replay a run's full trace")
async def agent_run(run_id: str) -> dict[str, object]:
    """The 'how we got this' panel, replayable after the fact.

    A trace you can re-read once the answer is on screen is what turns
    explainability from a claim into something a judge can check.
    """
    run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id} in this process's memory")
    return run


@router.get("/agent/runs", summary="Recent runs")
async def agent_runs() -> dict[str, object]:
    return {
        "runs": [
            {
                "run_id": r["run_id"],
                "thread_id": r["thread_id"],
                "question": r["question"],
                "finished_at": r["finished_at"],
                "event_count": len(r["events"]),  # type: ignore[arg-type]
            }
            for r in reversed(list(_RUNS.values()))
        ]
    }


@router.get("/agent/tools", summary="The capability catalogue the planner selects from")
async def agent_tools() -> dict[str, object]:
    """Exposed so the UI can show that tool choice is a decision over declared
    capabilities rather than a hardcoded sequence."""
    return {"tools": catalogue()}


@router.get("/agent/providers", summary="LLM provider chain and remaining budget")
async def agent_providers() -> dict[str, object]:
    return llm.budget_status()
