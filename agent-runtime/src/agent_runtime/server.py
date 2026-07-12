from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from agent_runtime.handler import invoke


app = FastAPI(title="Restaurant Ordering AgentCore Runtime")


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/invocations")
async def invocations(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return invoke(payload)
