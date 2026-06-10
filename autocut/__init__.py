"""AutoCut Pro — automatic review-video editor.

A small, focused package that turns raw review footage into a tight cut:

    media       — probe + codec-agnostic normalization (handles "any codec")
    transcribe  — speech-to-text via the HyperFrames CLI (Whisper)
    analyze     — score transcript segments and pick the best ones
    editor      — cut / concat / music-mix with ffmpeg (L-cut / J-cut seam)
    subtitles   — render captions with HyperFrames and composite them
    tools       — locate ffmpeg / ffprobe / hyperframes and run them safely

The whole pipeline is orchestrated from ``app.py``.
"""

__version__ = "1.0"
