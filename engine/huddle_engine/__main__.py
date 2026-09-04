"""Engine entry point.

  python -m huddle_engine serve            # HTTP API for the desktop app (env: HUDDLE_DATA_DIR/PORT/TOKEN)
  python -m huddle_engine mcp              # MCP server over stdio
  python -m huddle_engine process <audio>  # headless: import + process one file, print the notes
  python -m huddle_engine doctor           # environment report
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _logging(cfg) -> None:
    cfg.ensure_dirs()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stderr),
                logging.FileHandler(cfg.logs_dir / "engine.log", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parent_watchdog() -> None:
    """Exit when the desktop app that spawned us is gone (its pid is passed as HUDDLE_PARENT_PID).
    Prevents orphaned engines after crashes or dev reloads."""
    import os
    import threading
    import time

    raw = os.getenv("HUDDLE_PARENT_PID")
    if not raw or not raw.isdigit():
        return
    parent = int(raw)

    def loop():
        while True:
            time.sleep(2)
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                logging.getLogger("huddle").warning("parent app %d is gone — shutting down engine", parent)
                from .providers import ollama_runtime
                ollama_runtime.stop()
                os._exit(0)
            except PermissionError:
                pass   # exists, but not ours to signal

    threading.Thread(target=loop, name="huddle-parent-watchdog", daemon=True).start()


def cmd_serve(args) -> int:
    import uvicorn
    _parent_watchdog()

    from .settings import EngineConfig
    cfg = EngineConfig()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir)
    if args.port:
        cfg.port = args.port
    _logging(cfg)
    import os
    os.environ["HUDDLE_DATA_DIR"] = str(cfg.data_dir)
    uvicorn.run("huddle_engine.app:app", host=cfg.host, port=cfg.port, log_level="info", access_log=False)
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import main
    from .settings import EngineConfig
    cfg = EngineConfig()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)   # stdout is the MCP transport
    main(cfg)
    return 0


def cmd_process(args) -> int:
    from .context import EngineContext
    from .services import exports
    from .services import meetings as ms
    from .settings import EngineConfig
    cfg = EngineConfig()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir)
    _logging(cfg)
    ctx = EngineContext(cfg)
    ctx.registry.quick_check()
    m = ms.import_file(ctx.db, cfg, args.audio, args.title)
    ctx.jobs.enqueue(m.id)
    last = None
    while True:
        job = ms.get_job(ctx.db, m.id)
        if job and job.current_stage != last:
            last = job.current_stage
            if last:
                print(f"● {last} …", file=sys.stderr)
        if job and job.state in ("ready", "failed"):
            break
        time.sleep(0.5)
    for name, s in job.stages.items():
        if s.status == "skipped" and not s.error:
            continue  # on-demand stage (refining, action items) that was never asked for
        mark = {"done": "✓", "failed": "✗", "skipped": "○", "pending": "○"}.get(s.status, "?")
        print(f"{mark} {name}: {s.detail or s.error or ''}", file=sys.stderr)
    body, _ = exports.export(ctx.db, m.id, args.format)
    print(body)
    return 0 if job.state == "ready" else 1


def cmd_doctor(args) -> int:
    from .context import EngineContext
    from .providers.compute import compute_devices
    from .resolver import ResolverContext, additional_bytes, resolve_all
    from .settings import EngineConfig
    cfg = EngineConfig()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir)
    logging.basicConfig(level=logging.WARNING)
    ctx = EngineContext(cfg, start_jobs=False)
    ctx.registry.full_scan()
    print(f"data dir : {cfg.data_dir}")
    print(f"schema   : v{ctx.db.schema_version}")
    print(f"hardware : {json.dumps(ctx.hardware)}")
    for d in compute_devices():
        print(f"  device : {d.name} [{d.backend}] available={d.available} recommended={d.recommended}")
    for p in ctx.registry.providers():
        print(f"provider : {p.name:28s} {p.status}  {p.detail}")
    for m in ctx.registry.models():
        print(f"model    : {m.task:13s} {m.source:12s} {m.format:12s} {'compatible' if m.compatible else 'incompatible':12s} {m.name}")
    res = resolve_all(ResolverContext(ctx.registry, ctx.settings(), ctx.hardware.get('memoryBytes')))
    for r in res:
        print(f"resolve  : {r.task:13s} {r.status:18s} {r.provider or '-':14s} {r.model.name if r.model else (r.download.name if r.download else '-')}  ({r.reason})")
    print(f"additional download required: {additional_bytes(res) / 1e9:.2f} GB")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="huddle-engine", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("serve", cmd_serve), ("mcp", cmd_mcp), ("doctor", cmd_doctor)):
        sp = sub.add_parser(name)
        sp.add_argument("--data-dir", default=None)
        if name == "serve":
            sp.add_argument("--port", type=int, default=None)
        sp.set_defaults(func=fn)
    sp = sub.add_parser("process")
    sp.add_argument("audio")
    sp.add_argument("--title", default=None)
    sp.add_argument("--data-dir", default=None)
    sp.add_argument("--format", default="md", choices=["md", "txt", "json", "srt"])
    sp.set_defaults(func=cmd_process)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
