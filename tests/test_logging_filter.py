"""The stale-callback log filter.

A browser tab left open on a different Dash app that previously owned this port
keeps polling `/_dash-update-component` with that app's callback signature.
Dash raises KeyError and Flask logs a full traceback for each one, every couple
of minutes, forever - burying real errors in `errors.log`. Any stale or hostile
client can trigger it, so the application has to be immune rather than trusting
clients to behave.
"""

from __future__ import annotations

import logging

from oceanpulse.ui.app import _UnknownCallbackFilter

# The exact output signature observed coming from a stale SeismoPulse tab.
STALE_OUTPUT = (
    "..pulse-kpis.children...pulse-map-flat.figure...pulse-map-globe.figure.."
    ".pulse-flat-wrap.style...pulse-globe-wrap.style...pulse-ticker.children.."
    ".pulse-histogram.figure.."
)


def _flask_style_record() -> logging.LogRecord:
    """Flask puts the useful text in exc_info, not in the message."""
    error = KeyError(f"Callback function not found for output '{STALE_OUTPUT}'.")
    record = logging.LogRecord(
        name="oceanpulse.ui.app",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Exception on /_dash-update-component [POST]",
        args=(),
        exc_info=(KeyError, error, None),
    )
    return record


def test_unknown_callback_records_are_suppressed():
    stale = _UnknownCallbackFilter()
    assert stale.filter(_flask_style_record()) is False


def test_only_one_summary_per_interval():
    """The condition is worth knowing once, not five hundred times."""
    stale = _UnknownCallbackFilter(interval_seconds=3600.0)
    for _ in range(50):
        assert stale.filter(_flask_style_record()) is False
    # First match reports; the remaining 49 are counted, not logged.
    assert stale._suppressed == 49


def test_real_errors_still_get_through():
    """Suppression must be surgical. A genuine error must never be swallowed."""
    stale = _UnknownCallbackFilter()
    genuine = logging.LogRecord(
        name="oceanpulse.ingest.daemon",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="ingestion cycle failed: database is locked",
        args=(),
        exc_info=None,
    )
    assert stale.filter(genuine) is True


def test_matches_on_message_as_well_as_exception():
    """Some paths log the text directly rather than through exc_info."""
    stale = _UnknownCallbackFilter()
    record = logging.LogRecord(
        name="werkzeug",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Callback function not found for output 'status-daemon.children'.",
        args=(),
        exc_info=None,
    )
    assert stale.filter(record) is False
