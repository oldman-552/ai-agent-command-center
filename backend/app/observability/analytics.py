"""Cost & usage analytics (UX-04, spec Phase 20). Every number is an aggregate of recorded usage and cost rows.

Costs come only from `cost_records` (priced models); usage without a configured price is counted as tokens and
reported as `unpriced_requests`, never as zero cost. Mock usage is excluded unless explicitly requested, and then
reported separately.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, cast, func, select

from app.models import Agent, CostRecord, Project, Provider, UsageRecord, User
from app.security.auth import DB, require

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
Group = Literal["provider", "model", "agent", "project", "day"]


def _key(group: str):
    return {"provider": UsageRecord.provider_id, "model": UsageRecord.model, "agent": UsageRecord.agent_id,
            "project": UsageRecord.project_id, "day": cast(UsageRecord.ts, Date)}[group]


@router.get("/usage")
def usage(db: DB, user: User = Depends(require("dashboard.read")), group: Group = "provider",
          days: int = Query(default=30, ge=1, le=366), include_mock: bool = False) -> dict:
    org = user.organization_id
    since = datetime.now(UTC) - timedelta(days=days)
    key = _key(group)
    conds = [UsageRecord.organization_id == org, UsageRecord.ts >= since]
    if not include_mock:
        conds.append(UsageRecord.is_mock.is_(False))
    rows = db.execute(
        select(key.label("k"), UsageRecord.is_mock,
               func.count(UsageRecord.id), func.sum(UsageRecord.input_tokens), func.sum(UsageRecord.output_tokens),
               func.sum(UsageRecord.cached_tokens), func.sum(CostRecord.amount_usd),
               func.count(UsageRecord.id).filter(CostRecord.id.is_(None)), func.avg(UsageRecord.duration_ms),
               func.sum(UsageRecord.retry_count))
        .outerjoin(CostRecord, CostRecord.usage_record_id == UsageRecord.id)
        .where(*conds).group_by(key, UsageRecord.is_mock)).all()
    labels: dict = {}
    ids = [r[0] for r in rows if r[0] is not None]
    if group in ("provider", "agent", "project") and ids:
        model = {"provider": Provider, "agent": Agent, "project": Project}[group]
        labels = dict(db.execute(select(model.id, model.name).where(model.id.in_(ids),
                                                                     model.organization_id == org)).all())
    items = []
    for k, is_mock, n, tin, tout, cached, cost, unpriced, dur, retries in rows:
        items.append({"key": str(k) if k is not None else None,
                      "label": labels.get(k, str(k) if k is not None else "—") if group != "day" else str(k),
                      "is_mock": bool(is_mock), "requests": int(n), "input_tokens": int(tin or 0),
                      "output_tokens": int(tout or 0), "cached_tokens": int(cached or 0),
                      "cost_usd": str(cost) if cost is not None else None, "unpriced_requests": int(unpriced or 0),
                      "avg_duration_ms": round(float(dur)) if dur is not None else None,
                      "retries": int(retries or 0)})
    items.sort(key=lambda x: (x["label"] if group == "day" else "", -(float(x["cost_usd"] or 0)),
                              -(x["input_tokens"] + x["output_tokens"])))
    totals = {"requests": sum(i["requests"] for i in items if not i["is_mock"]),
              "tokens": sum(i["input_tokens"] + i["output_tokens"] for i in items if not i["is_mock"]),
              "cost_usd": str(sum((float(i["cost_usd"] or 0) for i in items if not i["is_mock"]), 0.0)),
              "unpriced_requests": sum(i["unpriced_requests"] for i in items if not i["is_mock"])}
    return {"group": group, "days": days, "include_mock": include_mock, "items": items, "totals": totals}
