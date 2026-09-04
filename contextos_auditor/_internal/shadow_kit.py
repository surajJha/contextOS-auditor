"""Vendored from `dashboard/shadow_kit.py` in the toku monorepo -- unchanged
estimation logic, copied so this package installs standalone (see this
package's README for the vendoring note).

Observes baseline tool events (especially write_file) and estimates what
those writes would have cost if the paid Kit's edit_file hunk tool had been
attached. Does not mutate agent behaviour — estimate only.

Honesty: this cannot model trajectory change (retries, different explore
paths). UI must label results Estimated unless a measured A/B report is
attached.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from contextos_auditor._internal import tokens as tk
from contextos_auditor._internal.pricing import PRICING_SNAPSHOT_DATE, PRICING_SOURCE_URL


@dataclass(frozen=True)
class HunkEstimate:
    path: str
    start_line: int
    end_line: int
    old_str: str
    new_str: str
    full_tokens: int
    hunk_tokens: int
    waste_tokens: int

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_hunk(path: str, old_text: str, new_text: str) -> HunkEstimate:
    """Line-anchored hunk that edit_file would need to turn old → new."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    if old_text == new_text:
        return HunkEstimate(
            path=path,
            start_line=1,
            end_line=max(1, len(old_lines)),
            old_str="",
            new_str="",
            full_tokens=tk.count_text(new_text or ""),
            hunk_tokens=0,
            waste_tokens=tk.count_text(new_text or ""),
        )

    # Longest common prefix/suffix → contiguous replace block.
    i = 0
    while (
        i < len(old_lines)
        and i < len(new_lines)
        and old_lines[i] == new_lines[i]
    ):
        i += 1
    j = 0
    while (
        j < (len(old_lines) - i)
        and j < (len(new_lines) - i)
        and old_lines[-(j + 1)] == new_lines[-(j + 1)]
    ):
        j += 1

    old_mid = old_lines[i : len(old_lines) - j if j else len(old_lines)]
    new_mid = new_lines[i : len(new_lines) - j if j else len(new_lines)]
    start = i + 1
    end = max(start, len(old_lines) - j) if old_lines else 1
    if not old_mid and new_mid:
        # Pure insert at end of prefix.
        end = start
    old_str = "\n".join(old_mid)
    new_str = "\n".join(new_mid)
    # Compare full-file rewrite payload to the hunk body the Kit would send.
    # (Tool schema/path overhead is tiny vs either payload; ignore it here.)
    full_tokens = tk.count_text(new_text or "")
    hunk_tokens = tk.count_text(old_str) + tk.count_text(new_str)
    return HunkEstimate(
        path=path,
        start_line=start,
        end_line=end,
        old_str=old_str,
        new_str=new_str,
        full_tokens=full_tokens,
        hunk_tokens=hunk_tokens,
        waste_tokens=max(0, full_tokens - hunk_tokens),
    )


#: Kit tool name -> the lever it names, IF the result text confirms it
#: actually fired (not a failed/no-op call). Order here is also the order
#: `shadow_session`'s `levers_fired` list uses when multiple fire in one
#: session (read-side first, then write-side, then navigation/output-side).
_LEVER_RULES: tuple[tuple[str, str, str | None], ...] = (
    ("read_file", "skeleton_read", "signature-only skeleton"),
    ("edit_file", "hunk_edit", "[agentcost-kit: edited"),
    ("smart_patch", "smart_patch", "smart_patch applied"),
    # KIT-007: append_to_file's success message starts "[agentcost-kit:
    # appended" (its no-op message starts "[agentcost-kit: append_to_file
    # no-op" — deliberately does NOT match this needle, same "don't credit
    # a no-op" discipline as edit_file/smart_patch above).
    ("append_to_file", "append_no_anchor", "[agentcost-kit: appended"),
    # KIT-011: success message starts "[agentcost-kit: inserted 1 element"
    # (failed/declined messages start "...failed"/"...declined" and don't
    # match, same "don't credit what didn't fire" rule as every lever).
    ("insert_into_array", "structural_array_insert", "[agentcost-kit: inserted"),
    ("find_def", "symbol_lookup", None),
    ("find_refs", "symbol_lookup", None),
    ("grep", "grep_lookup", None),
    ("list_dir", "folded_listing", None),
    ("run_shell", "shell_output_bound", None),
)


def _levers_fired_from_tool_calls(tool_calls: list[dict]) -> set[str]:
    """KIT-504: which Kit lever(s) actually engaged in this turn, inferred
    from the real tool-call sequence already recorded in events.jsonl — no
    re-run against the framework. A tool name matching a lever is not
    enough on its own: `edit_file`/`smart_patch` calls that failed or were
    a no-op (see `kit/tools.py`'s idempotency guard) must NOT be credited,
    the same way KIT-002 made "no skeleton fired on this YAML" an asserted
    fact instead of a silent maybe."""
    fired: set[str] = set()
    for t in tool_calls:
        name = t.get("name")
        result = t.get("result_text")
        result_str = result if isinstance(result, str) else ""
        for tool_name, lever, needle in _LEVER_RULES:
            if name != tool_name:
                continue
            if needle is None or needle in result_str:
                fired.add(lever)
    return fired


def shadow_session(events: list[dict]) -> dict:
    """Fold an events.jsonl-shaped list into actual vs Kit opportunity totals."""
    file_state: dict[str, str] = {}
    writes: list[dict] = []
    dupes: list[dict] = []
    turns: list[dict] = []
    actual_nano = 0
    actual_prompt = 0
    actual_completion = 0
    actual_total_tokens = 0
    waste_tokens = 0
    levers_fired: set[str] = set()
    actual_usd = 0.0
    usd_fully_priced = True  # flips to False the first turn with tokens but no dated price

    for ev in events:
        kind = ev.get("kind")
        if kind == "turn":
            usage = ev.get("usage") or {}
            cost = ev.get("cost") or {}
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or (prompt + completion))
            nano = int(cost.get("total_nano_aiu") or 0)
            actual_prompt += prompt
            actual_completion += completion
            actual_total_tokens += total
            actual_nano += nano
            usd = cost.get("estimated_usd")
            if usd is not None:
                actual_usd += float(usd)
            elif total > 0:
                usd_fully_priced = False
            tool_calls = ev.get("tool_calls") or []
            levers_fired |= _levers_fired_from_tool_calls(tool_calls)
            turns.append(
                {
                    "turn": ev.get("turn"),
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                    "nano_aiu": nano,
                    "tools": [t.get("name") for t in tool_calls],
                }
            )
            for t in tool_calls:
                name = t.get("name")
                args = t.get("args") or {}
                if name == "read_file" and args.get("path"):
                    # Prefer explicit content captured on the event; else keep prior.
                    content = t.get("result_text")
                    if isinstance(content, str):
                        path = str(args["path"])
                        # A re-read of content already in the transcript is
                        # pure duplicate spend: it buys no new information
                        # and is re-sent as prompt tokens on every turn that
                        # follows. Record it here; the per-turn multiplier
                        # can only be applied once we know how many turns
                        # the session actually ran.
                        if file_state.get(path) == content and content:
                            dupes.append(
                                {
                                    "turn": ev.get("turn"),
                                    "path": path,
                                    "tokens": tk.count_text(content),
                                }
                            )
                        file_state[path] = content
                if name == "write_file" and args.get("path") is not None:
                    path = str(args["path"])
                    new_text = str(args.get("content") or "")
                    old_text = file_state.get(path, "")
                    hunk = estimate_hunk(path, old_text, new_text)
                    waste_tokens += hunk.waste_tokens
                    writes.append(
                        {
                            "turn": ev.get("turn"),
                            **hunk.as_dict(),
                        }
                    )
                    file_state[path] = new_text

    # Duplicate-context waste. A redundant read of N tokens landing on turn
    # T is re-sent in the prompt of every later turn, so its true cost is
    # N x (number of turns it was carried through), not N. This is the
    # dominant waste category in real ReAct loops -- counting only
    # write-hunk waste understates the opportunity by an order of
    # magnitude, which makes the free auditor's number irreconcilable with
    # the measured savings the paid tool actually delivers.
    #
    # Two deliberate conservatism guards:
    #   1. Providers with prompt caching bill repeated prefixes at a
    #      discount, so on those this over-states. We do not know from the
    #      trace whether caching was active.
    #   2. The total is clamped to observed prompt tokens, so the estimate
    #      can never claim more waste than the session demonstrably spent.
    last_turn = max((int(t.get("turn") or 0) for t in turns), default=0)
    dup_waste_tokens = 0
    for d in dupes:
        carried = max(1, last_turn - int(d.get("turn") or 0) + 1)
        d["turns_carried"] = carried
        d["waste_tokens"] = int(d["tokens"]) * carried
        dup_waste_tokens += d["waste_tokens"]
    dup_waste_tokens = min(dup_waste_tokens, actual_prompt)

    write_waste_tokens = waste_tokens
    waste_tokens = write_waste_tokens + dup_waste_tokens

    cost_per_token = (
        actual_nano / actual_total_tokens if actual_total_tokens else 0.0
    )
    kit_nano = max(0, int(round(actual_nano - waste_tokens * cost_per_token)))

    # Framework adapters (LangGraph/CrewAI/AutoGen/OpenAI Agents SDK) usually
    # have no real per-model billing unit to attach (no Copilot total_nano_aiu,
    # no live provider invoice) — actual_nano stays 0 rather than inventing a
    # price table. Fall back to a token-fraction estimate so the Auditor still
    # shows a meaningful number, clearly labeled as token-based, not dollar-based.
    basis = "nano_aiu" if actual_nano > 0 else "tokens"
    if actual_nano > 0:
        save_pct = (1 - kit_nano / actual_nano) * 100.0
    elif actual_total_tokens > 0:
        save_pct = (waste_tokens / actual_total_tokens) * 100.0
    else:
        save_pct = 0.0

    return {
        "actual": {
            "prompt_tokens": actual_prompt,
            "completion_tokens": actual_completion,
            "total_tokens": actual_total_tokens,
            "total_nano_aiu": actual_nano,
            # AUD-011: dated $ estimate, only populated when every turn's
            # model had a citable price in _internal/pricing.py -- None
            # (not 0) otherwise, so a partially-priced multi-model session
            # never silently under-reports.
            "estimated_usd": (
                round(actual_usd, 6)
                if usd_fully_priced and actual_total_tokens > 0
                else None
            ),
        },
        "pricing": {
            "source": PRICING_SOURCE_URL,
            "snapshot_date": PRICING_SNAPSHOT_DATE,
        },
        "kit_estimate": {
            "total_nano_aiu": kit_nano,
            "write_waste_tokens": write_waste_tokens,
            "duplicate_context_waste_tokens": dup_waste_tokens,
            "waste_tokens": waste_tokens,
            "label": "estimated",
            "basis": basis,
        },
        "save_pct": round(save_pct, 1),
        "writes": writes,
        "duplicate_reads": dupes,
        "turns": turns,
        "levers_fired": sorted(levers_fired),
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
