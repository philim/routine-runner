#!/usr/bin/env python3
"""Routine Runner CLI — a single entry point for common operations.

Examples
--------
    python run.py serve                     # run the app (http://127.0.0.1:8000)
    python run.py serve --reload            # dev mode, auto-reload
    python run.py serve --host 0.0.0.0 --port 9000
    python run.py seed                      # seed children + routines
    python run.py migrate                   # apply DB migrations only
    python run.py test                      # run the test suite
    python run.py info                      # show resolved config / paths

The ``run.sh`` and ``run.bat`` wrappers forward their arguments here, activating
a local virtualenv if one is present.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _load_dotenv() -> None:
    """Minimal .env loader so `python run.py` picks up local config without deps."""
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,  # single worker — the in-process event bus requires it (spec §8.2)
        log_level=args.log_level,
    )
    return 0


def cmd_seed(_args: argparse.Namespace) -> int:
    from scripts.seed_routines import main as seed_main

    seed_main()
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    from app.config import load_config
    from app.db import main_db

    config = load_config()
    main_db.bootstrap(config)
    print(f"Migrations applied. Data dir: {config.data_dir}")
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    from app.config import load_config

    config = load_config()
    print("Routine Runner configuration")
    print(f"  data_dir        : {config.data_dir}")
    print(f"  main_db_path    : {config.main_db_path}")
    print(f"  instance_db_path: {config.instance_db_path}")
    print(f"  household_id    : {config.household_id}")
    print(f"  notify_backend  : {config.notify_backend}")
    print(f"  ntfy_topic      : {config.ntfy_topic or '(none)'}")
    return 0


def run_pytest(pytest_args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "pytest", *pytest_args])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py", description="Routine Runner service runner"
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="run the web service (default)")
    p_serve.add_argument("--host", default=os.environ.get("RR_HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("RR_PORT", "8000")))
    p_serve.add_argument("--reload", action="store_true", help="auto-reload on changes")
    p_serve.add_argument("--log-level", default=os.environ.get("RR_LOG_LEVEL", "info"))
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("seed", help="seed children and routines").set_defaults(func=cmd_seed)
    sub.add_parser("migrate", help="apply DB migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("info", help="show resolved config").set_defaults(func=cmd_info)

    # `test` is documented here, but handled before argparse (see main) so that
    # pytest flags like `-q` pass through untouched.
    sub.add_parser("test", help="run the test suite (extra args pass to pytest)")

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    raw = list(sys.argv[1:] if argv is None else argv)
    # Handle `test` before argparse so pytest flags (e.g. -q, -k) pass through.
    if raw and raw[0] == "test":
        return run_pytest(raw[1:])

    parser = build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "command", None):
        # default to `serve` with defaults when invoked bare
        args = parser.parse_args(["serve"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
