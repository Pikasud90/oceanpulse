#!/usr/bin/env python3
"""OceanPulse entry point: web interface, ingestion daemon, or both.

Shutdown is handled explicitly. Closing a terminal without a signal handler
leaves the ingestion thread running, holding the SQLite write-ahead log open,
and the next launch then finds a locked database and a daemon it did not
start. `SIGINT` and `SIGTERM` (plus `SIGBREAK` on Windows, which is what a
console close actually sends there) stop the worker, checkpoint the WAL and
release the lock file.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Make `src/` importable without requiring an install step.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from oceanpulse.config import ALLOWED_POLL_INTERVALS, load_config  # noqa: E402
from oceanpulse.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("oceanpulse.run")

_shutdown = threading.Event()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="OceanPulse — self-hosted marine data ingestion and analytics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "commands:\n"
            "  (none)      web interface and ingestion daemon (default)\n"
            "  daemon      ingestion only, no web interface\n"
            "  gazetteer   build the offline port database, then exit\n"
        ),
    )
    parser.add_argument(
        "command", nargs="?", default="serve", choices=["serve", "daemon", "gazetteer"]
    )
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="web port (default 8050)")
    parser.add_argument("--no-daemon", action="store_true", help="web interface only")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--debug", action="store_true", help="Dash debug mode")
    parser.add_argument(
        "--force", action="store_true", help="gazetteer: rebuild even if one exists"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        choices=ALLOWED_POLL_INTERVALS,
        default=None,
        help="minutes between polls",
    )
    return parser.parse_args(argv)


def port_is_free(host: str, port: int) -> bool:
    """Check the port before Dash tries to bind it.

    Without this, a port clash surfaces as a Werkzeug traceback ending in
    "Address already in use" — which does not tell a non-developer what to do,
    and is easy to hit when a previous run is still alive or another app owns
    8050.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR: we want to know whether a real listener is there,
        # not whether we could squeeze in beside one.
        probe.bind(("" if host == "0.0.0.0" else host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def open_storage_or_explain(db_path: Path):
    """Open the database, turning corruption into instructions.

    A truncated or non-SQLite file raises `sqlite3.DatabaseError: file is not a
    database`, which as a bare traceback looks like a bug in the application
    rather than a damaged file the user can simply move aside.
    """
    import sqlite3

    from oceanpulse.storage.sqlite_backend import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        storage.initialise()
    except sqlite3.DatabaseError as exc:
        print(
            f"\n  ERROR: {db_path} could not be opened ({exc}).\n"
            f"\n  The file looks damaged or is not a SQLite database. Every"
            f"\n  observation in it can be re-collected from the public sources,"
            f"\n  so the fix is simply to move it aside:\n"
            f"\n      mv \"{db_path}\" \"{db_path}.broken\"\n"
            f"\n  Then start OceanPulse again. Your port database and ocean mask"
            f"\n  are separate files and are not affected.\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return storage


def check_sqlite_features() -> None:
    """Warn early if this Python's SQLite lacks FTS5.

    Place search is the only feature that needs it, and it fails at *build*
    time with an opaque OperationalError. Saying so at startup is kinder than
    letting a 90-second download end in a stack trace.
    """
    import sqlite3

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE _probe USING fts5(a)")
    except sqlite3.OperationalError:
        log.warning(
            "This Python's SQLite was built without FTS5, so the offline port "
            "search cannot be built. Everything else works. Installing Python "
            "from python.org (rather than a minimal system build) resolves it."
        )
    finally:
        connection.close()


def install_signal_handlers(on_stop) -> None:
    def handler(signum, _frame):  # noqa: ANN001
        log.info("received signal %s, shutting down", signum)
        on_stop()
        # Cleaning up is not the same as stopping. The web server blocks the
        # main thread inside app.run(), and nothing above returns control to
        # it, so without this the process keeps serving after its daemon and
        # database have been shut down. Ctrl+C happens to work because
        # Werkzeug installs its own SIGINT handler; SIGTERM - what a service
        # manager and `kill` actually send - would otherwise hang until the
        # stop timeout expired and the process was killed outright.
        # Raising in the handler unwinds the blocking call.
        raise SystemExit(0)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # Not the main thread, or unsupported on this platform.
            pass


def command_gazetteer(config, force: bool) -> int:
    from oceanpulse.gazetteer.build import build_gazetteer, gazetteer_exists
    from oceanpulse.ingest import runner
    from oceanpulse.ingest.grid import OceanMask

    if gazetteer_exists(config.ports_db_path) and not force:
        print(f"Port database already present at {config.ports_db_path}")
        return 0

    loop = runner.get_loop()
    client = runner.get_client(config)
    mask = OceanMask.load(config.ocean_mask_path)
    if mask is None:
        print("No ocean mask yet — coastal filtering will be skipped this build.")

    try:
        report = loop.run(
            build_gazetteer(
                client,
                config.ports_db_path,
                mask=mask,
                geonames_dataset=config.geonames_dataset,
                include_cities=config.gazetteer_include_cities,
                coastal_max_km=config.coastal_max_km,
            ),
            timeout=900.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Gazetteer build failed: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.shutdown()

    print(
        f"Gazetteer built: {report['entries']:,} entries "
        f"({report['wpi']:,} ports, {report['geonames']:,} coastal places)"
    )
    for problem in report.get("errors", []):
        print(f"  note: {problem}")
    return 0


def start_daemon(config, storage):
    """Start the ingestion daemon on the shared background loop."""
    from oceanpulse.ingest import runner
    from oceanpulse.ingest.daemon import IngestionDaemon

    # Pass storage so the request budget survives restarts.
    client = runner.get_client(config, storage)
    daemon = IngestionDaemon(config, storage, client)
    future = runner.get_loop().submit(daemon.run())
    return daemon, future


def ensure_gazetteer_background(config) -> None:
    """Build the port database on first run without blocking startup."""
    from oceanpulse.gazetteer.build import build_gazetteer, gazetteer_exists
    from oceanpulse.ingest import runner
    from oceanpulse.ingest.grid import OceanMask

    if gazetteer_exists(config.ports_db_path):
        return

    def worker() -> None:
        # Give the daemon a moment to derive the ocean mask, which makes the
        # coastal filter meaningful. Not worth blocking on.
        time.sleep(20)
        try:
            report = runner.get_loop().run(
                build_gazetteer(
                    runner.get_client(config),
                    config.ports_db_path,
                    mask=OceanMask.load(config.ocean_mask_path),
                    geonames_dataset=config.geonames_dataset,
                    include_cities=config.gazetteer_include_cities,
                    coastal_max_km=config.coastal_max_km,
                ),
                timeout=1200.0,
            )
            log.info(
                "gazetteer ready: %d entries (%d ports, %d coastal places)",
                report["entries"],
                report["wpi"],
                report["geonames"],
            )
        except Exception as exc:  # noqa: BLE001 - never kill startup over this
            log.warning("background gazetteer build failed: %s", exc)

    threading.Thread(target=worker, name="gazetteer-build", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    overrides = {
        "host": args.host,
        "port": args.port,
        "debug": True if args.debug else None,
        "open_browser": False if args.no_browser else None,
        "poll_interval_minutes": args.poll_interval,
    }
    config = load_config({k: v for k, v in overrides.items() if v is not None})
    config.ensure_dirs()
    setup_logging(config.log_dir)

    if args.command == "gazetteer":
        return command_gazetteer(config, args.force)

    from oceanpulse.ingest import runner

    check_sqlite_features()
    storage = open_storage_or_explain(config.db_path)
    if args.poll_interval:
        storage.set_setting("poll_interval_minutes", str(args.poll_interval))

    daemon = None
    if args.command == "daemon" or not args.no_daemon:
        daemon, _ = start_daemon(config, storage)
        ensure_gazetteer_background(config)

    def stop_everything() -> None:
        if _shutdown.is_set():
            return
        _shutdown.set()
        if daemon is not None:
            daemon.request_stop()
        # Give the current cycle a moment to finish its write.
        time.sleep(0.6)
        runner.shutdown()
        storage.close()

    install_signal_handlers(stop_everything)

    if args.command == "daemon":
        print("OceanPulse ingestion daemon running. Press Ctrl+C to stop.")
        try:
            while not _shutdown.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop_everything()
        return 0

    if not port_is_free(config.host, config.port):
        print(
            f"\n  ERROR: port {config.port} on {config.host} is already in use.\n"
            f"\n  Something is listening there already — most often a previous"
            f"\n  OceanPulse that is still running, or another app that claimed"
            f"\n  the port. Either stop it, or pick a different port:\n"
            f"\n      ./run.sh --port 9000        (macOS / Linux)"
            f"\n      run.bat --port 9000         (Windows)\n"
            f"\n  To find the process holding it:\n"
            f"\n      lsof -iTCP:{config.port} -sTCP:LISTEN   (macOS / Linux)"
            f"\n      netstat -ano | findstr :{config.port}    (Windows)\n",
            file=sys.stderr,
        )
        stop_everything()
        return 1

    from oceanpulse.ui.app import build_app

    app, _services = build_app(config)

    url = f"http://{'localhost' if config.host in ('0.0.0.0', '127.0.0.1') else config.host}:{config.port}"
    if config.host == "0.0.0.0":
        print(
            "\n  WARNING: binding to 0.0.0.0 exposes this interface, and your database,\n"
            "  to every machine on your network. There is no authentication.\n"
        )
    print(f"\n  OceanPulse is running at {url}\n")

    if config.open_browser and not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        app.run(
            host=config.host,
            port=config.port,
            debug=config.debug,
            use_reloader=False,  # a reloader would start a second daemon
        )
    except KeyboardInterrupt:
        pass
    finally:
        stop_everything()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
