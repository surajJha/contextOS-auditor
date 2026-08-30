"""contextos-auditor CLI.

    contextos-auditor watch                 # live terminal table, auto-picks
                                              # the most recently-written
                                              # session under ./.contextos/audit
    contextos-auditor watch --once           # single snapshot, no poll loop
    contextos-auditor watch --html out.html  # also write a static HTML report
    contextos-auditor watch --serve          # localhost-only live view (SSE)
    contextos-auditor report <session-id>    # one-shot final summary
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


def cmd_watch(args: argparse.Namespace) -> int:
    session_dir = _resolve_session_dir(args)
    if session_dir is None:
        print(
            f"No session found under {args.audit_root} (no */events.jsonl yet).\n"
            f"Attach the Auditor in your agent code first -- see "
            f"`contextos-auditor doctor` for the exact snippet for your framework."
        )
        return 1
    if not session_dir.is_dir():
        print(f"Session directory not found: {session_dir}")
        return 1

    if args.serve:
        return serve_session(session_dir, poll_interval=args.poll_interval, port=args.port)

    def _poll_once() -> None:
        session_id, meta, result = snapshot(session_dir)
        print(render_terminal(session_id, meta, result))
        if args.html:
            out_path = Path(args.html)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(render_html(session_id, meta, result, args.poll_interval))

    if args.once:
        _poll_once()
        return 0

    try:
        while True:
            _poll_once()
            print()
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")
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
    sub = ap.add_subparsers(dest="command", required=True)

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

    p_doctor = sub.add_parser("doctor", help="check which framework SDKs/hooks are available")
    p_doctor.set_defaults(func=cmd_doctor)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
