def to_global_time(local_seconds: float, chunk_offset: float) -> float:
    """Convert local segment time to global video time by adding chunk offset.

    Examples:
        to_global_time(30.0, 0) -> 30.0
        to_global_time(15.0, 600) -> 615.0
        to_global_time(0.0, 3540) -> 3540.0
    """
    return chunk_offset + local_seconds


def format_hhmmss(seconds: float) -> str:
    """Format a duration in seconds to HH:MM:SS string.

    Examples:
        format_hhmmss(615.0) -> "00:10:15"
        format_hhmmss(0.0) -> "00:00:00"
        format_hhmmss(3600.0) -> "01:00:00"
        format_hhmmss(3661.0) -> "01:01:01"
    """
    if seconds < 0:
        raise ValueError(f"format_hhmmss requires non-negative seconds, got {seconds}")
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
