# This is AI generated code
"""Helper functions used by the TEC logic module.

Subset of ``pyscript/modules/helpers.py`` -- only the
notification-formatting pieces that ``logic.py``
imports. The full helpers module ships under pyscript
(where the pyscript-side wrappers live); this minimal
copy keeps the native integration self-contained.

Kept as a near-verbatim lift for two reasons:
1. Behavioural parity -- token expansion has to match
   what pyscript-side users have configured today.
2. The pure-function tests in
   ``tests/test_trigger_entity_controller.py`` exercise
   ``logic.py`` which transitively exercises these
   functions. Keeping the implementations identical
   means those tests continue to pass against the
   lifted ``logic.py``.
"""

from __future__ import annotations

from datetime import datetime


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.
    result = template
    result = result.replace("YYYY", f"{dt.year:04d}")
    result = result.replace("YY", f"{dt.year % 100:02d}")
    result = result.replace("MM", f"{dt.month:02d}")
    result = result.replace("DD", f"{dt.day:02d}")
    result = result.replace("HH", f"{dt.hour:02d}")
    result = result.replace("mm", f"{dt.minute:02d}")
    result = result.replace("ss", f"{dt.second:02d}")
    return result


def format_notification(
    text: str,
    prefix: str,
    suffix: str,
    current_time: datetime,
) -> str:
    """Format notification with prefix/suffix and timestamp tokens."""
    formatted_prefix = format_timestamp(prefix, current_time)
    formatted_suffix = format_timestamp(suffix, current_time)
    return f"{formatted_prefix}{text}{formatted_suffix}"
