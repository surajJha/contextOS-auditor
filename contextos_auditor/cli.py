"""contextos-auditor CLI.

    contextos-auditor demo                   # synthetic session, no agent
                                              # or API key required -- the
                                              # fastest way to see output
    contextos-auditor demo --serve           # ...in the localhost dashboard
    contextos-auditor watch                 # live terminal table, auto-picks
                                              # the most recently-written
                                              # session under ./.contextos/audit
    contextos-auditor watch --once           # single snapshot, no poll loop
    contextos-auditor watch --html out.html  # also write a static HTML report
    contextos-auditor watch --serve          # localhost-only live view (SSE)
    contextos-auditor report <session-id>    # one-shot final summary
    contextos-auditor history                # list past sessions + aggregate stats
    contextos-auditor doctor                 # which framework SDKs/hooks are
                                              # available in this environment
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.compat import _TESTED, _parse_version, installed_version
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import render_html, render_terminal
from contextos_auditor.server import serve_session

_DEFAULT_AUDIT_ROOT = Path.cwd() / ".contextos" / "audit"
_DEFAULT_POLL_SECONDS = 5.0

# name -> (import path, extra name, compat key) used by `doctor`
_FRAMEWORKS: dict[str, tuple[str, str, str]] = {
    "crewai": ("crewai", "crewai", "crewai"),
    "langgraph": ("langchain_core", "langgraph", "langgraph"),
    "openai-agents": ("agents", "openai-agents", "openai_agents"),
    "autogen": ("autogen_core", "autogen", "autogen"),
}


def find_running_session(audit_root: Path) -> Path | None:
    candidates = sorted(
        audit_root.glob("*/events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0].parent if candidates else None


def read_session_meta(session_dir: Path) -> dict[str, Any]:
    import json

    meta_path = session_dir / "session.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return {}


def snapshot(session_dir: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    events = load_events(session_dir)
    meta = read_session_meta(session_dir)
    result = shadow_session(events)
    session_id = meta.get("id", session_dir.name)
    return session_id, meta, result


def _resolve_session_dir(args: argparse.Namespace) -> Path | None:
    audit_root = Path(args.audit_root)
    if getattr(args, "session", None):
        return Path(args.session)
    if getattr(args, "session_id", None):
        return audit_root / args.session_id
    return find_running_session(audit_root)


def _wait_for_session_dir(args: argparse.Namespace, poll_seconds: float = 1.0) -> Path | None:
    """`watch`/`watch --serve` are commonly started *before* the agent run
    (open the dashboard, then kick off the agent) -- just as often as the
    reverse order. Poll for a session to appear instead of failing
    immediately, so starting the dashboard first isn't a dead end.
    Returns None only on Ctrl-C (caller should exit 130 in that case)."""
    session_dir = _resolve_session_dir(args)
    if session_dir is not None:
        return session_dir
    print(
        f"No session found yet under {args.audit_root} -- waiting for one to start "
        f"(Ctrl-C to stop).\nAttach the Auditor in your agent code first -- see "
        f"`contextos-auditor doctor` for the exact snippet for your framework."
    )
    try:
        waited = 0.0
        nudged = False
        while session_dir is None:
            time.sleep(poll_seconds)
            waited += poll_seconds
            # An indefinite silent wait is indistinguishable from a hang.
            # Say something once, then keep waiting -- starting the
            # dashboard before the agent is a legitimate workflow, so a
            # hard timeout would break as many people as it helps.
            if not nudged and waited >= 30.0:
                nudged = True
                print(
                    "\nStill nothing after 30s. Two things worth checking:\n"
                    "  - is the auditor actually attached? `contextos-auditor doctor`\n"
                    "  - is your agent running from this directory? sessions are written\n"
                    f"    under {args.audit_root}, relative to the agent's working directory\n"
                    "\nOr, to see what this looks like without an agent at all:\n"
                    "  contextos-auditor demo --serve\n"
                )
            session_dir = _resolve_session_dir(args)
    except KeyboardInterrupt:
        print("\nStopped waiting.")
        return None
    print(f"Session found: {session_dir.name}\n")
    return session_dir


def cmd_watch(args: argparse.Namespace) -> int:
    if args.once:
        # Scriptable one-shot use (e.g. CI) -- fail fast rather than block.
        session_dir = _resolve_session_dir(args)
        if session_dir is None:
            print(
                f"No session found under {args.audit_root} (no */events.jsonl yet).\n"
                f"Attach the Auditor in your agent code first -- see "
                f"`contextos-auditor doctor` for the exact snippet for your framework."
            )
            return 1
    else:
        session_dir = _wait_for_session_dir(args)
        if session_dir is None:
            return 130
    if not session_dir.is_dir():
        print(f"Session directory not found: {session_dir}")
        return 1

    if args.serve:
        return serve_session(session_dir, poll_interval=args.poll_interval, port=args.port)

    def _poll_once() -> bool:
        """Returns True if the session has reached a terminal status."""
        session_id, meta, result = snapshot(session_dir)
        print(render_terminal(session_id, meta, result))
        if args.html:
            out_path = Path(args.html)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(render_html(session_id, meta, result, args.poll_interval))
        return meta.get("status") in ("finished", "error")

    if args.once:
        _poll_once()
        return 0

    try:
        while True:
            done = _poll_once()
            if done:
                print("\nSession finished -- stopping (rerun with --once for a static snapshot).")
                break
            print()
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Generate and show a synthetic session -- no agent, SDK or key needed."""
    from contextos_auditor.demo import run_demo

    audit_root = Path(args.audit_root)
    print(
        "Generating a synthetic demo session -- no agent, no API key, no network.\n"
        "This is fabricated input run through the real analysis, so the numbers\n"
        "below are computed the same way they are for your own agents.\n"
    )
    session_id = run_demo(audit_root)
    session_dir = audit_root / session_id
    sid, meta, result = snapshot(session_dir)

    if args.serve:
        print(f"Serving {sid} at http://127.0.0.1:{args.port} (Ctrl-C to stop)\n")
        return serve_session(session_dir, poll_interval=_DEFAULT_POLL_SECONDS, port=args.port)

    print(render_terminal(sid, meta, result))
    if args.html:
        out_path = Path(args.html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_html(sid, meta, result, _DEFAULT_POLL_SECONDS))
        print(f"\nWrote {out_path}")
    print(
        f"\nThat was a demo. To do this on your own agent:\n"
        f"  1. `contextos-auditor doctor`  -- get the one-line snippet for your framework\n"
        f"  2. run your agent\n"
        f"  3. `contextos-auditor watch`   -- live view of the real thing\n"
        f"\nDelete the demo data any time: rm -rf {session_dir}"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    session_dir = _resolve_session_dir(args)
    if session_dir is None or not session_dir.is_dir():
        print(f"Session not found: {args.session_id!r} under {args.audit_root}")
        return 1
    session_id, meta, result = snapshot(session_dir)
    print(render_terminal(session_id, meta, result))
    if args.html:
        out_path = Path(args.html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_html(session_id, meta, result, _DEFAULT_POLL_SECONDS))
        print(f"\nWrote {out_path}")
    return 0


def _all_session_dirs(audit_root: Path) -> list[Path]:
    """Every session directory under `audit_root`, newest first -- a session
    counts even if it's still `running` (has events.jsonl but no terminal
    status yet), matching `find_running_session`'s definition of "exists"."""
    dirs = {p.parent for p in audit_root.glob("*/session.json")}
    return sorted(dirs, key=lambda p: (p / "session.json").stat().st_mtime, reverse=True)


def cmd_history(args: argparse.Namespace) -> int:
    """AUD-014: `watch`/`report` only ever look at the single
    most-recently-modified session -- there was no way to see past runs or
    a trend across them without opening each session.json/events.jsonl by
    hand. Lists every session under --audit-root with basic aggregate
    stats, newest first. No database, no new files written -- purely a
    read-and-summarize view over the JSONL each session already writes."""
    audit_root = Path(args.audit_root)
    session_dirs = _all_session_dirs(audit_root)
    if not session_dirs:
        print(f"No sessions found under {audit_root}")
        return 0
    if args.limit:
        session_dirs = session_dirs[: args.limit]

    header = (
        f"{'session':<30}  {'framework':<12}  {'model':<16}  {'status':<9}  "
        f"{'turns':>5}  {'tokens':>8}  {'cost':>10}  {'save%':>7}"
    )
    print(header)
    print("-" * len(header))
    for session_dir in session_dirs:
        session_id, meta, result = snapshot(session_dir)
        usd = result["actual"].get("estimated_usd")
        usd_str = f"${usd:.4f}" if usd is not None else "n/a"
        print(
            f"{session_id:<30.30}  {str(meta.get('framework', '?')):<12.12}  "
            f"{str(meta.get('model', '?')):<16.16}  {str(meta.get('status', '?')):<9.9}  "
            f"{len(result['turns']):>5}  {result['actual']['total_tokens']:>8}  "
            f"{usd_str:>10}  {result['save_pct']:>+6.1f}%"
        )
    total_sessions = len(_all_session_dirs(audit_root))
    if args.limit and total_sessions > len(session_dirs):
        print(f"\n(showing {len(session_dirs)} most recent of {total_sessions} -- see --limit)")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("contextos-auditor doctor -- framework hook availability\n")
    any_found = False
    for name, (module, extra, compat_key) in _FRAMEWORKS.items():
        try:
            __import__(module)
            version = installed_version(compat_key)
            spec = _TESTED.get(compat_key)
            status = "ready to attach"
            if version and spec:
                v = _parse_version(version)
                if not (spec.min_version <= v < spec.max_version_exclusive):
                    lo = ".".join(str(p) for p in spec.min_version)
                    hi = ".".join(str(p) for p in spec.max_version_exclusive)
                    status = f"detected {version} -- OUTSIDE tested range [{lo}, {hi}), double-check your numbers"
                else:
                    status = f"detected {version} -- within tested range, ready to attach"
            print(f"  [ok]      {name:<14} SDK {status}")
            any_found = True
        except ImportError:
            print(f"  [missing] {name:<14} not installed -- `pip install contextos-auditor[{extra}]`")
    if not any_found:
        print(
            "\nNo supported framework SDK found in this environment. Install one of the\n"
            "extras above, or run `pip install contextos-auditor[all]` to get every hook."
        )
    print(
        "\nSnippets:\n"
        "  crewai:         from contextos_auditor.crewai import attach\n"
        "  langgraph:      from contextos_auditor.langgraph import AuditorCallback\n"
        "  openai-agents:  from contextos_auditor.openai_agents import attach\n"
        "  autogen:        from contextos_auditor.autogen import new_session, wrap_client, audit_tool\n"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="contextos-auditor")
    sub = ap.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--audit-root", default=str(_DEFAULT_AUDIT_ROOT))

    p_watch = sub.add_parser("watch", parents=[common], help="live view of the most recent (or a named) session")
    p_watch.add_argument("--session", help="path to a session directory (overrides auto-pick)")
    p_watch.add_argument("--session-id", help="session id under --audit-root (overrides auto-pick)")
    p_watch.add_argument("--poll-interval", type=float, default=_DEFAULT_POLL_SECONDS)
    p_watch.add_argument("--html", help="also write a self-contained auto-refreshing HTML snapshot here")
    p_watch.add_argument("--serve", action="store_true", help="serve a localhost-only live view (Server-Sent Events, no polling meta-refresh)")
    p_watch.add_argument("--port", type=int, default=8765)
    p_watch.add_argument("--once", action="store_true", help="print a single snapshot and exit")
    p_watch.set_defaults(func=cmd_watch)

    p_report = sub.add_parser("report", parents=[common], help="one-shot final summary for a finished session")
    p_report.add_argument("session_id", nargs="?", help="session id under --audit-root (defaults to the most recent)")
    p_report.add_argument("--html", help="write a static HTML report here")
    p_report.set_defaults(func=cmd_report)

    p_history = sub.add_parser("history", parents=[common], help="list past sessions under --audit-root with aggregate stats")
    p_history.add_argument("--limit", type=int, default=20, help="show at most this many sessions, newest first (0 = no limit)")
    p_history.set_defaults(func=cmd_history)

    p_doctor = sub.add_parser("doctor", help="check which framework SDKs/hooks are available")
    p_doctor.set_defaults(func=cmd_doctor)

    p_demo = sub.add_parser(
        "demo", parents=[common],
        help="see it work on a synthetic session -- no agent or API key required",
    )
    p_demo.add_argument("--serve", action="store_true", help="open the localhost dashboard instead of printing to the terminal")
    p_demo.add_argument("--port", type=int, default=8765)
    p_demo.add_argument("--html", help="write a self-contained HTML report here")
    p_demo.set_defaults(func=cmd_demo)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if getattr(args, "func", None) is None:
        # Bare `contextos-auditor` used to exit 2 with an argparse usage
        # error, which tells a first-time user nothing about what to do
        # next. Point them at the one command that works with no setup.
        print(
            "contextos-auditor -- see what your AI agent is really spending.\n"
            "\n"
            "New here? This needs no agent, no API key and no network:\n"
            "\n"
            "    contextos-auditor demo --serve\n"
            "\n"
            "Already have an agent?\n"
            "\n"
            "    contextos-auditor doctor    # the one-line snippet for your framework\n"
            "    contextos-auditor watch     # live view once your agent is running\n"
            "\n"
            "Full command list: contextos-auditor --help"
        )
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
