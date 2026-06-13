"""AutoCut — automatic review-video editor (v1.4, fully offline).

    media       — probe + codec-agnostic normalization (handles "any codec")
    analyze     — pick spoken parts by silence detection + trim dead air
    editor      — cut / concat / music-mix / MP3 (ffmpeg, L-cut / J-cut seam)
    tools       — locate ffmpeg / ffprobe and run them safely
    storage     — choose a roomy data dir + build the output folder
    licensing   — machine-locked activation (Ed25519)
    updater     — signed OTA code updates

No AI / no Node / no internet required — the whole pipeline is ffmpeg-only.
Orchestrated from ``app.py``.
"""

__version__ = "1.4"
