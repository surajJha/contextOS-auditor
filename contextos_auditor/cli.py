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
import math
import sys
import time
from pathlib import Path
from typing import Any

from contextos_auditor import __version__
from contextos_auditor._internal.audit_emit import load_events_with_stats
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
    # BUG-C14: a concurrent agent can rotate/delete a session directory
    # between glob() and the sort's stat() call, which used to raise
    # FileNotFoundError out of every command. A file we can't stat is
    # treated as oldest (mtime 0) rather than fatal.
    candidates = sorted(
        audit_root.glob("*/events.jsonl"),
        key=_safe_mtime,
        reverse=True,
    )
    return candidates[0].parent if candidates else None


def _safe_mtime(path: Path) -> float:
    """BUG-C14: stat() failures (vanished/unreadable file) sort as oldest."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def read_session_meta(session_dir: Path) -> dict[str, Any]:
    import json

    meta_path = session_dir / "session.json"
    if not meta_path.is_file():
        return {}
    try:
        # BUG-F-001: session.json is written as utf-8; read it back the same
        # way instead of falling through to the platform locale encoding.
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def snapshot(session_dir: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    events, unreadable = load_events_with_stats(session_dir)
    meta = read_session_meta(session_dir)
    result = shadow_session(events)
    session_id = meta.get("id", session_dir.name)
    # BUG-F-002/C5: carry the dropped-line count into the renderers so a
    # truncated/torn events.jsonl is reported ("N unreadable lines
    # skipped") instead of silently producing a smaller-but-plausible
    # total. `meta` is the only dict both renderers already receive.
    if unreadable:
        meta = dict(meta)
        meta["unreadable_lines"] = unreadable
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


def _write_html_snapshot(
    html_path: str,
    session_id: str,
    meta: dict[str, Any],
    result: dict[str, Any],
    *,
    poll_seconds: float,
    auto_refresh: bool = False,
) -> Path:
    """BUG-F-001: every dashboard write goes through here so the encoding is
    fixed in exactly one place. `Path.write_text()` with no `encoding=`
    uses the *platform locale* encoding (cp1252 on a default Windows
    install) while the template hardcodes `<meta charset="utf-8">`, so any
    emoji / CJK path / curly quote coming out of an agent crashed `demo`,
    `report` and `watch --html` with UnicodeEncodeError on Windows."""
    out_path = Path(html_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(session_id, meta, result, poll_seconds, auto_refresh=auto_refresh),
        encoding="utf-8",
    )
    return out_path


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
        # BUG-C11: `--html` used to be silently dropped when combined with
        # `--serve` (this early return made its branch unreachable), even
        # though both flags are documented and accepted together. Writing
        # the snapshot first and *then* serving is strictly more useful
        # than rejecting the combination: the user still gets the live
        # dashboard, plus a shareable file they explicitly asked for. The
        # server blocks until Ctrl-C, so the file must be written first.
        if args.html:
            out_path = _write_html_snapshot(
                args.html, *snapshot(session_dir), poll_seconds=args.poll_interval
            )
            print(f"Wrote {out_path} (static snapshot; the live view below keeps updating)")
        return serve_session(session_dir, poll_interval=args.poll_interval, port=args.port)

    def _poll_once() -> bool:
        """Returns True if the session has reached a terminal status."""
        session_id, meta, result = snapshot(session_dir)
        print(render_terminal(session_id, meta, result))
        if args.html:
            _write_html_snapshot(
                args.html,
                session_id,
                meta,
                result,
                poll_seconds=args.poll_interval,
                # BUG-R4: this is the one caller that really does rewrite the
                # file on every poll, so the meta-refresh tells the truth here.
                auto_refresh=not args.once,
            )
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
        # BUG-C11: `demo --serve --html` used to drop the HTML file too.
        if args.html:
            out_path = _write_html_snapshot(
                args.html, sid, meta, result, poll_seconds=_DEFAULT_POLL_SECONDS
            )
            print(f"Wrote {out_path}")
        print(f"Serving {sid} at http://127.0.0.1:{args.port} (Ctrl-C to stop)\n")
        return serve_session(session_dir, poll_interval=_DEFAULT_POLL_SECONDS, port=args.port)

    print(render_terminal(sid, meta, result))
    if args.html:
        out_path = _write_html_snapshot(
            args.html, sid, meta, result, poll_seconds=_DEFAULT_POLL_SECONDS
        )
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
        out_path = _write_html_snapshot(
            args.html, session_id, meta, result, poll_seconds=_DEFAULT_POLL_SECONDS
        )
        print(f"\nWrote {out_path}")
    return 0


def _all_session_dirs(audit_root: Path) -> list[Path]:
    """Every session directory under `audit_root`, newest first -- a session
    counts even if it's still `running` (has events.jsonl but no terminal
    status yet), matching `find_running_session`'s definition of "exists"."""
    dirs = {p.parent for p in audit_root.glob("*/session.json")}
    # BUG-C14: mtime via _safe_mtime -- a session directory rotated away by
    # a concurrent agent between glob() and stat() must not abort the scan.
    return sorted(dirs, key=lambda p: _safe_mtime(p / "session.json"), reverse=True)


def _history_rows(session_dirs: list[Path]) -> tuple[list[dict], list[str], int]:
    """Read each session once and return plain row dicts.

    Extracted so the terminal table and the `--html` view are rendered from
    exactly the same data. The caveats below (skipped sessions, unreadable
    lines) are the whole reason: an HTML history that quietly dropped a
    corrupt session would look like a *complete* record of your runs, which
    is precisely the kind of confident-but-wrong output this tool exists to
    avoid.
    """
    rows: list[dict] = []
    skipped: list[str] = []
    unreadable_lines = 0
    for session_dir in session_dirs:
        try:
            session_id, meta, result = snapshot(session_dir)
        except Exception as e:
            # BUG-C14: an unreadable/corrupt session used to kill `history`
            # mid-table (rc=1, half-printed header). Skip the row, but name
            # it below so the omission is visible rather than silent.
            skipped.append(f"{session_dir.name}: {type(e).__name__}: {e}")
            continue
        session_unreadable = int(meta.get("unreadable_lines") or 0)
        unreadable_lines += session_unreadable
        rows.append(
            {
                "session_id": session_id,
                "framework": str(meta.get("framework", "?")),
                "model": str(meta.get("model", "?")),
                "status": str(meta.get("status", "?")),
                "turns": len(result["turns"]),
                "total_tokens": result["actual"]["total_tokens"],
                "estimated_usd": result["actual"].get("estimated_usd"),
                "save_pct": result["save_pct"],
                "unreadable_lines": session_unreadable,
            }
        )
    return rows, skipped, unreadable_lines


def cmd_history(args: argparse.Namespace) -> int:
    """AUD-014: `watch`/`report` only ever look at the single
    most-recently-modified session -- there was no way to see past runs or
    a trend across them without opening each session.json/events.jsonl by
    hand. Lists every session under --audit-root with basic aggregate
    stats, newest first. No database, no new files written -- purely a
    read-and-summarize view over the JSONL each session already writes.

    LNCH-014: `--html` writes the same table with sparklines, because a
    trend across runs is the one thing a fixed-width terminal table is
    genuinely bad at showing."""
    audit_root = Path(args.audit_root)
    limit = getattr(args, "limit", 0) or 0
    if limit < 0:
        # BUG-C9: `--limit -1` used to slice `dirs[:-1]`, quietly showing
        # zero sessions and exiting 0. argparse rejects negatives before we
        # get here; this guard covers direct programmatic calls.
        print("--limit must be >= 0 (0 = no limit)")
        return 2
    session_dirs = _all_session_dirs(audit_root)
    html_path = getattr(args, "html", None)
    if not session_dirs:
        print(f"No sessions found under {audit_root}")
        if html_path:
            # Still write the file. A stale report left on disk from a
            # previous run would be read as current.
            if _write_history_html(html_path, [], [], 0, audit_root) is None:
                return 1
        return 0
    if limit:
        session_dirs = session_dirs[:limit]

    rows, skipped, unreadable_lines = _history_rows(session_dirs)

    header = (
        f"{'session':<30}  {'framework':<12}  {'model':<16}  {'status':<9}  "
        f"{'turns':>5}  {'tokens':>8}  {'cost':>10}  {'save%':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        usd = row["estimated_usd"]
        usd_str = f"${usd:.4f}" if usd is not None else "n/a"
        print(
            f"{row['session_id']:<30}  {row['framework']:<12.12}  "
            f"{row['model']:<16.16}  {row['status']:<9.9}  "
            f"{row['turns']:>5}  {row['total_tokens']:>8}  "
            f"{usd_str:>10}  {row['save_pct']:>+6.1f}%"
        )
    for note in skipped:
        print(f"(skipped unreadable session {note})")
    if unreadable_lines:
        # BUG-F-002/C5: never let a truncated file look like a smaller run.
        print(
            f"({unreadable_lines} unreadable lines skipped across these sessions -- "
            f"their totals are incomplete)"
        )
    total_sessions = len(_all_session_dirs(audit_root))
    if limit and total_sessions > len(session_dirs):
        print(f"\n(showing {len(session_dirs)} most recent of {total_sessions} -- see --limit)")
    if html_path:
        written = _write_history_html(html_path, rows, skipped, unreadable_lines, audit_root)
        if written is None:
            return 1
        print(f"Wrote {written}")
    return 0


def _write_history_html(
    path: str, rows: list[dict], skipped: list[str], unreadable_lines: int, audit_root: Path
) -> Path | None:
    from contextos_auditor.report import render_history_html

    target = Path(path)
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_history_html(rows, skipped, unreadable_lines, audit_root),
            encoding="utf-8",
        )
    except OSError as e:
        # Writing the report is a side output; failing it must report a
        # non-zero exit rather than a traceback, and must not discard the
        # terminal table already printed above.
        print(f"Could not write {target} ({e})")
        return None
    return target



def cmd_doctor(_args: argparse.Namespace) -> int:
    print("contextos-auditor doctor -- framework hook availability\n")
    any_found = False
    broken: list[str] = []
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
            print(f'  [missing] {name:<14} not installed -- `python -m pip install "contextos-auditor[{extra}]"`')
        except Exception as e:
            # BUG-C6: a broken transitive dependency (common with
            # crewai/autogen) raises something other than ImportError
            # straight out of the one command whose whole job is to
            # diagnose a sick environment. Report the row and keep going
            # through the remaining frameworks.
            #
            # Exit code stays 0 deliberately: `doctor` reports on optional
            # extras, and a non-zero exit would make "one broken optional
            # SDK" indistinguishable from "the auditor itself is broken"
            # for anyone running it in CI or a health check. The [broken]
            # row is the signal; the exit code is about doctor itself.
            print(f"  [broken]  {name:<14} {type(e).__name__}: {e}")
            broken.append(name)
    if broken:
        print(
            f"\n{len(broken)} SDK(s) are installed but fail to import: {', '.join(broken)}.\n"
            "That's an environment problem in those packages, not in the auditor --\n"
            "try reinstalling them. Everything else above still works."
        )
    if not any_found:
        print(
            "\nNo supported framework SDK found in this environment. Install one of the\n"
            'extras above, or run `python -m pip install "contextos-auditor[all]"` to get every hook.'
        )
    print(
        "\nSnippets:\n"
        "  crewai:         from contextos_auditor.crewai import attach\n"
        "  langgraph:      from contextos_auditor.langgraph import AuditorCallback\n"
        "  openai-agents:  from contextos_auditor.openai_agents import attach\n"
        "  autogen:        from contextos_auditor.autogen import new_session, wrap_client, audit_tool\n"
    )
    return 0


def _port_arg(value: str) -> int:
    """BUG-C10: `--port 99999` reached socket.bind() and blew up with a raw
    OverflowError (only OSError was caught), so the user got a traceback
    instead of a message. Validate it at parse time -- argparse then emits
    a clean error and exits 2."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer port number") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be between 1 and 65535 (got {port})")
    return port


def _limit_arg(value: str) -> int:
    """BUG-C9: a negative --limit sliced from the *end* of the list, so
    `history --limit -1` printed zero sessions and exited 0. 0 keeps its
    documented "no limit" meaning."""
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if limit < 0:
        raise argparse.ArgumentTypeError(f"--limit must be >= 0 (0 = no limit), got {limit}")
    return limit


def _poll_interval_arg(value: str) -> float:
    try:
        interval = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("poll interval must be a positive finite number") from None
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("poll interval must be a positive finite number")
    return interval


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="contextos-auditor")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--audit-root", default=str(_DEFAULT_AUDIT_ROOT))

    p_watch = sub.add_parser("watch", parents=[common], help="live view of the most recent (or a named) session")
    p_watch.add_argument("--session", help="path to a session directory (overrides auto-pick)")
    p_watch.add_argument("--session-id", help="session id under --audit-root (overrides auto-pick)")
    p_watch.add_argument("--poll-interval", type=_poll_interval_arg, default=_DEFAULT_POLL_SECONDS)
    p_watch.add_argument("--html", help="also write a self-contained auto-refreshing HTML snapshot here")
    p_watch.add_argument("--serve", action="store_true", help="serve a localhost-only live view (Server-Sent Events, no polling meta-refresh)")
    p_watch.add_argument("--port", type=_port_arg, default=8765)
    p_watch.add_argument("--once", action="store_true", help="print a single snapshot and exit")
    p_watch.set_defaults(func=cmd_watch)

    p_report = sub.add_parser("report", parents=[common], help="one-shot final summary for a finished session")
    p_report.add_argument("session_id", nargs="?", help="session id under --audit-root (defaults to the most recent)")
    p_report.add_argument("--html", help="write a static HTML report here")
    p_report.set_defaults(func=cmd_report)

    p_history = sub.add_parser("history", parents=[common], help="list past sessions under --audit-root with aggregate stats")
    p_history.add_argument("--limit", type=_limit_arg, default=20, help="show at most this many sessions, newest first (0 = no limit)")
    p_history.add_argument("--html", help="also write the history as a self-contained HTML page with sparklines")
    p_history.set_defaults(func=cmd_history)

    p_doctor = sub.add_parser("doctor", help="check which framework SDKs/hooks are available")
    p_doctor.set_defaults(func=cmd_doctor)

    p_demo = sub.add_parser(
        "demo", parents=[common],
        help="see it work on a synthetic session -- no agent or API key required",
    )
    p_demo.add_argument("--serve", action="store_true", help="open the localhost dashboard instead of printing to the terminal")
    p_demo.add_argument("--port", type=_port_arg, default=8765)
    p_demo.add_argument("--html", help="write a self-contained HTML report here")
    p_demo.set_defaults(func=cmd_demo)

    return ap


def main(argv: list[str] | None = None) -> int:
    # Legacy Windows pipes cannot encode every agent path. Preserve their
    # encoding, escaping unsupported characters instead of aborting the CLI.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if getattr(args, "func", None) is None:        # Bare `contextos-auditor` used to exit 2 with an argparse usage
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
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as e:
        # BUG-F-002/C5, BUG-C10, BUG-C14: the auditor is an observability
        # tool bolted onto someone else's run -- it must never dump a raw
        # traceback at them. Anything that still escapes a command becomes
        # one clear line and exit 1. (The traceback is still available via
        # CONTEXTOS_AUDITOR_TRACEBACK=1 for bug reports.)
        import os

        if os.environ.get("CONTEXTOS_AUDITOR_TRACEBACK"):
            raise
        print(f"contextos-auditor: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "Re-run with CONTEXTOS_AUDITOR_TRACEBACK=1 for the full traceback, and\n"
            "please report it: https://github.com/surajJha/contextOS-auditor/issues",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
