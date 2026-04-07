# This is AI generated code
"""Shared notification formatting helpers.

No PyScript runtime dependencies.

Provides timestamp token substitution and
prefix/suffix wrapping for notification messages.
"""

from datetime import datetime


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.  Uses str.replace instead of
    # re.sub + lambda because PyScript's AST evaluator
    # cannot resolve local variables in lambda closures.
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
    """Format notification with prefix/suffix and
    timestamp tokens."""
    formatted_prefix = format_timestamp(
        prefix,
        current_time,
    )
    formatted_suffix = format_timestamp(
        suffix,
        current_time,
    )
    return f"{formatted_prefix}{text}{formatted_suffix}"
