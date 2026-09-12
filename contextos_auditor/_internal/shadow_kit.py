"""Canonical shadow estimator for the standalone Auditor and dashboard.

Observes baseline tool events (especially write_file) and estimates what
those writes would have cost if the paid Kit's edit_file hunk tool had been
attached. Does not mutate agent behaviour — estimate only.

Honesty: this cannot model trajectory change (retries, different explore
paths). UI must label results Estimated unless a measured A/B report is
attached.
"""

from __future__ import annotations

import posixpath
from dataclasses import asdict, dataclass

from contextos_auditor._internal import tokens as tk
from contextos_auditor._internal.shadow_totals import SessionTotals


def _norm_path(path) -> str:
    """Canonical key for `file_state` / duplicate-read detection.

    BUG-B11: keying on the raw arg string meant `a.py`, `./a.py` and
    `dir/../a.py` were three different files, so genuine duplicate reads
    were silently under-reported. Normalising costs nothing and only ever
    merges keys that denote the same file.

    Backslashes are folded to `/` first so a Windows-style arg normalises
    the same way on POSIX (`posixpath.normpath` would otherwise treat the
    whole string as one segment). Case is preserved: this has to stay
    correct on case-sensitive filesystems, and a wrong merge would
    manufacture a duplicate that never happened -- the one error this
    product must never make.
    """
    text = str(path).replace("\\", "/").strip()
    if not text:
        return ""
    normed = posixpath.normpath(text)
    # normpath turns "./a.py" into "a.py" but leaves "a.py" alone; strip a
    # leading "./" that survives on inputs like "././".
    return normed[2:] if normed.startswith("./") else normed


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


def _observe_tools(
    tool_calls: list[dict],
    turn,
    file_state: dict[str, str],
    writes: list[dict],
    dupes: list[dict],
) -> None:
    """Reconstruct known file contents and collect attributable opportunities."""
    for tool in tool_calls:
        name = tool.get("name")
        args = tool.get("args") or {}
        if name == "read_file" and args.get("path"):
            content = tool.get("result_text")
            if isinstance(content, str):
                path = _norm_path(args["path"])
                if file_state.get(path) == content and content:
                    dupes.append({
                        "turn": turn,
                        "path": path,
                        "tokens": tk.count_text(content),
                    })
                file_state[path] = content
        if name == "write_file" and args.get("path") is not None:
            path = _norm_path(args["path"])
            new_text = str(args.get("content") or "")
            hunk = estimate_hunk(path, file_state.get(path, ""), new_text)
            writes.append({"turn": turn, **hunk.as_dict()})
            file_state[path] = new_text


def shadow_session(events: list[dict]) -> dict:
    """Fold events into observed usage and bounded, explicitly estimated waste."""
    totals = SessionTotals()
    file_state: dict[str, str] = {}
    writes: list[dict] = []
    dupes: list[dict] = []
    levers: set[str] = set()
    for event in events:
        if event.get("kind") != "turn":
            continue
        totals.record(event)
        tools = event.get("tool_calls") or []
        levers |= _levers_fired_from_tool_calls(tools)
        _observe_tools(tools, event.get("turn"), file_state, writes, dupes)
    return totals.summarize(writes, dupes, levers)
