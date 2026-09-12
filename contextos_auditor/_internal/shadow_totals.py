"""Billed usage aggregation and bounded opportunity accounting."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from contextos_auditor._internal.pricing import PRICING_SNAPSHOT_DATE, PRICING_SOURCE_URL


def _clamp_waste_rows(rows: list[dict], ceiling: int) -> int:
    """Keep every renderer's row sum equal to the bounded headline."""
    raw = sum(row["waste_tokens"] for row in rows)
    total = min(raw, max(0, ceiling))
    if raw > total:
        running = 0
        for row in rows[:-1]:
            row["waste_tokens"] = row["waste_tokens"] * total // raw
            running += row["waste_tokens"]
        rows[-1]["waste_tokens"] = total - running
        for row in rows:
            row["clamped"] = True
    return total


def _duplicate_waste(dupes: list[dict], turns: list[dict], ceiling: int) -> int:
    """Count only recorded, billed prompt carries, never synthetic flushes."""
    billed = sorted(
        int(t.get("turn") or 0)
        for t in turns
        if not t.get("synthetic") and t["total_tokens"] > 0
    )
    for row in dupes:
        # Turn IDs can have gaps. Subtracting IDs invents unrecorded calls;
        # a read flushed after the final LLM call has no billed carry.
        carried = len(billed) - bisect_left(billed, int(row.get("turn") or 0))
        row["turns_carried"] = carried
        row["waste_tokens"] = row["tokens"] * carried
    return _clamp_waste_rows(dupes, ceiling)


@dataclass
class SessionTotals:
    prompt: int = 0
    completion: int = 0
    tokens: int = 0
    nano: int = 0
    usd: float = 0.0
    fully_priced: bool = True
    turns: list[dict] = field(default_factory=list)

    def record(self, event: dict) -> None:
        usage = event.get("usage") or {}
        cost = event.get("cost") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        nano = int(cost.get("total_nano_aiu") or 0)
        self.prompt += prompt
        self.completion += completion
        self.tokens += total
        self.nano += nano
        usd = cost.get("estimated_usd")
        if usd is not None:
            self.usd += float(usd)
        elif total > 0:
            self.fully_priced = False
        self.turns.append({
            "turn": event.get("turn"),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "nano_aiu": nano,
            "tools": [t.get("name") for t in event.get("tool_calls") or []],
            "synthetic": bool(event.get("synthetic")),
        })

    def summarize(self, writes: list[dict], dupes: list[dict], levers: set[str]) -> dict:
        # Total-only providers have no prompt split. Using a zero prompt
        # ceiling would silently erase their detected duplicate context.
        dup_ceiling = self.prompt if self.prompt > 0 else self.tokens
        dup_waste = _duplicate_waste(dupes, self.turns, dup_ceiling)
        write_waste = _clamp_waste_rows(writes, self.tokens - dup_waste)
        waste = write_waste + dup_waste
        cost_per_token = self.nano / self.tokens if self.tokens else 0.0
        kit_nano = max(0, int(round(self.nano - waste * cost_per_token)))
        basis = "nano_aiu" if self.nano > 0 else "tokens"
        if self.nano > 0:
            save_pct = (1 - kit_nano / self.nano) * 100.0
        elif self.tokens > 0:
            save_pct = waste / self.tokens * 100.0
        else:
            save_pct = 0.0
        return {
            "actual": {
                "prompt_tokens": self.prompt,
                "completion_tokens": self.completion,
                "total_tokens": self.tokens,
                "total_nano_aiu": self.nano,
                # Never present a partially priced session as its full bill.
                "estimated_usd": (
                    round(self.usd, 6) if self.fully_priced and self.tokens > 0 else None
                ),
            },
            "pricing": {
                "source": PRICING_SOURCE_URL,
                "snapshot_date": PRICING_SNAPSHOT_DATE,
            },
            "kit_estimate": {
                "total_nano_aiu": kit_nano,
                "write_waste_tokens": write_waste,
                "duplicate_context_waste_tokens": dup_waste,
                "waste_tokens": waste,
                "label": "estimated",
                "basis": basis,
            },
            "save_pct": round(max(0.0, min(100.0, save_pct)), 1),
            "writes": writes,
            "duplicate_reads": dupes,
            "turns": self.turns,
            "levers_fired": sorted(levers),
            "disclaimer": (
                "Estimated opportunity only. Auditor does not change your bill. "
                "Savings apply when AgentCost Kit is attached (paid). Shadow "
                "cannot model trajectory changes."
                if basis == "nano_aiu"
                else "Estimated opportunity only, based on token counts (no "
                "real billing unit available for this session's model/provider). "
                "Auditor does not change your bill. Shadow cannot model "
                "trajectory changes."
            ),
        }
