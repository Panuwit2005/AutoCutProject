"""AutoCut Pro — Flask backend.

The heavy work runs in a background thread per job so the HTTP request returns
immediately and the browser polls ``/status/<job_id>`` for *real* progress
(no more fake countdowns).  The pipeline is:

    upload → normalize (any codec) → transcribe → pick best moments
           → cut → optional HyperFrames captions → package (zip / merged)

Every stage is defensive: a failure in transcription falls back to silence-based
cutting, and a failure in caption rendering still delivers the cut video.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

# Windows consoles default to a legacy code page (e.g. cp874) that cannot encode
# the emoji / Thai we log.  Force UTF-8 so logging never raises UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from autocut import (analyze, editor, licensing, media, storage,
                     tools, transcribe, updater)

# Route all temp/work files to the roomiest writable drive (avoids
# "No space left on device" on machines with a full system drive) and sweep up
# any leftovers from previous runs.  Safe/idempotent if the launcher already ran it.
storage.setup()
storage.purge_stale()

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024 * 1024  # 8 GB

# Where index.html / static live.  An applied OTA patch (AUTOCUT_CODE_DIR, set by
# the launcher) wins; then the frozen bundle (sys._MEIPASS); then source.
HERE = (os.environ.get("AUTOCUT_CODE_DIR")
        or getattr(sys, "_MEIPASS", None)
        or os.path.dirname(os.path.abspath(__file__)))
# Behind-the-scenes AI (word timing for smarter cuts — never shown to the user).
WHISPER_MODEL = os.environ.get("AUTOCUT_WHISPER_MODEL", "small")
LANGUAGE = os.environ.get("AUTOCUT_LANGUAGE", "th")

# In-memory job registry.
JOBS: dict[str, "Job"] = {}
JOBS_LOCK = threading.Lock()


# ===========================================================================
#  Job model
# ===========================================================================
class Job:
    def __init__(self, job_id: str, work_dir: str):
        self.id = job_id
        self.work_dir = work_dir
        self.status = "queued"          # queued | running | done | error
        self.stage = "เริ่มต้น"
        self.pct = 0
        self.message = ""
        self.logs: list[str] = []
        self.error: str | None = None
        self.output_dir: str | None = None     # folder where deliverables were saved
        self.result_name: str | None = None     # that folder's display name
        self.created = datetime.now(timezone.utc)
        self.started_at: float | None = None   # monotonic when work began (count-up timer)
        self._lock = threading.Lock()

    def log(self, msg: str):
        line = str(msg).rstrip()
        print(f"[{self.id[:8]}] {line}", flush=True)
        with self._lock:
            self.logs.append(line)
            self.logs = self.logs[-200:]

    def set(self, *, stage: str | None = None, pct: int | None = None,
            message: str | None = None):
        with self._lock:
            if stage is not None:
                self.stage = stage
            if pct is not None:
                self.pct = max(self.pct, min(100, pct))
            if message is not None:
                self.message = message
        if message:
            self.log(message)

    def _elapsed_seconds(self) -> int:
        """Wall-clock seconds since work began (drives the count-up timer)."""
        if not self.started_at:
            return 0
        import time as _t
        return max(0, int(_t.monotonic() - self.started_at))

    def snapshot(self) -> dict:
        elapsed = self._elapsed_seconds()
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "pct": self.pct,
                "message": self.message,
                "elapsed_seconds": elapsed,
                "output_dir": self.output_dir,
                "output_name": self.result_name,
                "error": self.error,
                "ready": self.status == "done",
            }


# ===========================================================================
#  Static serving
# ===========================================================================
@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(HERE, "static"), filename)


# ===========================================================================
#  Health / debug
# ===========================================================================
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Get the current storage folder, or set a customer-chosen one."""
    if request.method == "GET":
        return jsonify(storage.info())
    body = request.get_json(silent=True) or {}
    path = (body.get("data_dir") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "ไม่ได้ระบุโฟลเดอร์"}), 400
    res = storage.set_data_dir(path)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/settings/reset", methods=["POST"])
def settings_reset():
    return jsonify(storage.reset_data_dir())


@app.route("/pick-folder", methods=["POST"])
def pick_folder():
    """Open a native Windows folder dialog on this machine and apply the choice."""
    import subprocess

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--pick-folder"]
    else:
        cmd = [sys.executable, "-m", "autocut.folder_picker"]
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300,
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             creationflags=no_window)
    except (subprocess.TimeoutExpired, OSError) as e:
        return jsonify({"ok": False, "error": f"เปิดหน้าต่างเลือกโฟลเดอร์ไม่ได้: {e}"}), 500
    path = (res.stdout or "").strip().splitlines()[-1].strip() if res.stdout.strip() else ""
    if not path:
        return jsonify({"ok": False, "cancelled": True})
    out = storage.set_data_dir(path)
    return jsonify(out), (200 if out.get("ok") else 400)


@app.route("/license/status")
def license_status():
    # Best-effort: sync the admin kill-switch list when online (never blocks
    # offline use — a failure leaves the saved state untouched).
    try:
        licensing.refresh_revocation()
    except Exception:  # noqa: BLE001
        pass
    return jsonify(licensing.status())


@app.route("/license/activate", methods=["POST"])
def license_activate():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"activated": False, "machine_id": licensing.machine_id(),
                        "error": "กรุณากรอกคีย์"}), 400
    res = licensing.activate(key)
    return jsonify(res), (200 if res.get("activated") else 400)


@app.route("/update/status")
def update_status():
    """Is a newer signed version available online?"""
    return jsonify(updater.check())


@app.route("/update/apply", methods=["POST"])
def update_apply():
    """Download + verify + stage the patch; it activates on next launch."""
    info = updater.check(ttl=0)  # force a fresh check
    if not info.get("available"):
        return jsonify({"ok": False, "error": "ไม่มีอัปเดตใหม่"}), 400
    try:
        version = updater.stage(info["manifest"], info["base"])
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"อัปเดตไม่สำเร็จ: {e}"}), 500
    return jsonify({"ok": True, "version": version, "restart": True})


@app.route("/debug")
def debug():
    st = tools.status()
    return jsonify({
        "ffmpeg": st.ffmpeg or "❌ not found",
        "ffprobe": st.ffprobe or "❌ not found",
        "ai": "✅ whisper word-timing" if transcribe.available() else "⚠️ silence-cut fallback",
    })


# ===========================================================================
#  Start a job
# ===========================================================================
@app.route("/process", methods=["POST"])
def process_video():
    if not licensing.is_activated():
        return jsonify({"error": "ยังไม่ได้เปิดใช้งานโปรแกรม — กรุณากรอกคีย์เปิดใช้งานก่อน",
                        "need_activation": True}), 403

    videos = request.files.getlist("videos")
    if not videos:
        return jsonify({"error": "ไม่พบไฟล์วิดีโอ"}), 400

    if not tools.status().core_ok:
        return jsonify({"error": "ไม่พบ ffmpeg บนเครื่อง — ติดตั้ง ffmpeg ก่อนใช้งาน"}), 500

    # Free space from finished jobs before we start a new one.
    _purge_finished_jobs()

    job_id = uuid.uuid4().hex
    work_dir = tempfile.mkdtemp(prefix=f"autocut_{job_id[:8]}_")

    # Pre-flight disk check: the upload is saved once and then transcoded, so we
    # need roughly 2× the upload size free.  Refuse early with a clear message
    # rather than dying mid-pipeline with an opaque OSError.
    needed = (request.content_length or 0) * 2
    free = storage.free_bytes(work_dir)
    if needed and free and free < needed:
        shutil.rmtree(work_dir, ignore_errors=True)
        gb = needed / (1024 ** 3)
        drive = os.path.splitdrive(work_dir)[0] or "ดิสก์"
        return jsonify({"error": (
            f"พื้นที่ดิสก์ไม่พอ — ต้องการว่างประมาณ {gb:.1f} GB บนไดรฟ์ {drive} "
            f"(ว่างอยู่ {free / (1024 ** 3):.1f} GB) กรุณาลบไฟล์ออกแล้วลองใหม่"
        )}), 507

    # Persist uploads before returning (request context ends when we respond).
    try:
        video_paths = []
        for v in videos:
            name = _safe_name(v.filename) or f"video_{len(video_paths)}.mp4"
            path = os.path.join(work_dir, name)
            v.save(path)
            video_paths.append(path)

        # Order clips by file name (natural sort) so the customer never has to
        # arrange footage — naming them 01, 02, … (or DJI's timestamped names)
        # just works.
        video_paths.sort(key=_natural_key)

        music_path = None
        music = request.files.get("music")
        if music and music.filename:
            music_path = os.path.join(work_dir, _safe_name(music.filename))
            music.save(music_path)
    except OSError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        if e.errno == 28:  # ENOSPC
            return jsonify({"error": "พื้นที่ดิสก์ไม่พอระหว่างบันทึกไฟล์ — กรุณาลบไฟล์ออกแล้วลองใหม่"}), 507
        return jsonify({"error": f"บันทึกไฟล์ไม่สำเร็จ: {e}"}), 500

    opts = {
        "max_duration": _to_int(request.form.get("max_duration"), 60, 5, 600),
        "output_mode": request.form.get("output_mode", "zip"),
        "output_format": _valid_format(request.form.get("output_format", "mp4")),
        "sfx_on": request.form.get("sfx_on", "false").lower() == "true",
        "sfx_type": request.form.get("sfx_type", "pop"),
        "lj_cut_on": request.form.get("lj_cut_on", "false").lower() == "true",
        "lj_cut_mode": _valid_lj_mode(request.form.get("lj_cut_mode", "l")),
        "lj_overlap": _to_float(request.form.get("lj_overlap"), 0.5, 0.1, 1.0),
        "dead_air_on": request.form.get("dead_air_on", "true").lower() == "true",
        "dead_air_aggr": _valid_aggr(request.form.get("dead_air_aggr", "medium")),
        "music_path": music_path,
        # Background-music loudness the customer picked (1-100%) → 0.01-1.0.
        "music_volume": _to_int(request.form.get("music_volume"), 20, 1, 100) / 100.0,
        # 0 = deterministic "best" cut; any other value = a different variation
        # of the same footage ("🎲 สุ่มรูปแบบใหม่").
        "variation": _to_int(request.form.get("variation"), 0, 0, 10_000_000),
        "audio_extract": request.form.get("audio_extract", "false").lower() == "true",
        # Optional customer-chosen name for the project/clips; blank → "Project".
        "project_name": request.form.get("project_name", ""),
        # Output quality / size controls (all default to "keep source").
        "aspect": _valid_aspect(request.form.get("aspect", "auto")),
        "resolution": _valid_resolution(request.form.get("resolution", "auto")),
        "fps": _valid_fps(request.form.get("fps", "30")),
        "crf": _CRF.get(request.form.get("quality", "high"), 20),
    }

    job = Job(job_id, work_dir)
    with JOBS_LOCK:
        JOBS[job_id] = job

    thread = threading.Thread(target=_run_job, args=(job, video_paths, opts), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "ไม่พบงานนี้"}), 404
    return jsonify(job.snapshot())


@app.route("/open-folder/<job_id>", methods=["POST"])
def open_folder(job_id):
    """Reveal a finished job's output folder in the OS file manager.

    The server runs on the customer's own machine, so this pops their File
    Explorer straight to the saved clips.
    """
    job = JOBS.get(job_id)
    if not job or not job.output_dir or not os.path.isdir(job.output_dir):
        return jsonify({"ok": False, "error": "ไม่พบโฟลเดอร์ผลงาน"}), 404
    try:
        if os.name == "nt":
            os.startfile(job.output_dir)  # noqa: S606 — local, customer's own machine
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", job.output_dir])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", job.output_dir])
    except OSError as e:
        return jsonify({"ok": False, "error": f"เปิดโฟลเดอร์ไม่ได้: {e}"}), 500
    return jsonify({"ok": True, "path": job.output_dir})


# ===========================================================================
#  Pipeline
# ===========================================================================
def _run_job(job: Job, video_paths: list[str], opts: dict):
    job.status = "running"
    job.started_at = time.monotonic()
    try:
        result = _pipeline(job, video_paths, opts)
        job.output_dir = result["output_dir"]
        job.result_name = result["name"]
        job.status = "done"
        job.set(stage="เสร็จแล้ว", pct=100,
                message=f"✅ บันทึกสำเร็จ — {result['name']}")
        # Deliverables now live in the output folder; drop all the intermediates.
        shutil.rmtree(job.work_dir, ignore_errors=True)
    except OSError as e:
        job.status = "error"
        job.error = ("พื้นที่ดิสก์ไม่พอระหว่างประมวลผล — กรุณาลบไฟล์ออกแล้วลองใหม่"
                     if e.errno == 28 else str(e))
        job.set(stage="ผิดพลาด", message=f"❌ {job.error}")
        job.log(traceback.format_exc())
        shutil.rmtree(job.work_dir, ignore_errors=True)  # nothing to download
    except Exception as e:  # noqa: BLE001
        job.error = str(e)
        job.status = "error"
        job.set(stage="ผิดพลาด", message=f"❌ {e}")
        job.log(traceback.format_exc())
        shutil.rmtree(job.work_dir, ignore_errors=True)


def _purge_finished_jobs(max_age_min: float = 30.0) -> None:
    """Remove work dirs of done/errored jobs older than *max_age_min* minutes."""
    now = datetime.now(timezone.utc)
    with JOBS_LOCK:
        stale = [
            j for j in JOBS.values()
            if j.status in ("done", "error")
            and (now - j.created).total_seconds() > max_age_min * 60
        ]
        for j in stale:
            shutil.rmtree(j.work_dir, ignore_errors=True)
            JOBS.pop(j.id, None)


def _pipeline(job: Job, video_paths: list[str], opts: dict) -> dict:
    work_dir = job.work_dir
    fmt = opts["output_format"]
    max_duration = opts["max_duration"]
    log = job.log

    # 1. Probe + decide a single canvas for everything ----------------------
    job.set(stage="อ่านไฟล์วิดีโอ", pct=5, message="🔍 ตรวจสอบไฟล์และ codec…")
    infos = [media.probe(p) for p in video_paths]
    canvas = media.resolve_canvas(infos, opts["aspect"], opts["resolution"])
    fps, crf = opts["fps"], opts["crf"]
    log(f"🖼 Canvas: {canvas[0]}x{canvas[1]} @ {fps}fps crf{crf} | {len(infos)} clip(s)")

    all_clips: list[str] = []          # final per-source clip files
    for idx, (src, info) in enumerate(zip(video_paths, infos)):
        base_pct = 10 + int(idx / max(1, len(video_paths)) * 65)
        tag = os.path.basename(src)
        log(f"\n🎬 [{idx+1}/{len(video_paths)}] {tag}")

        # 2. Normalize (handles any codec) ----------------------------------
        job.set(stage="แปลงไฟล์ให้มาตรฐาน", pct=base_pct,
                message=f"⚙️ แปลง codec/{info.vcodec or '?'} → มาตรฐาน ({idx+1}/{len(video_paths)})")
        norm = os.path.join(work_dir, f"norm_{idx:02d}.mp4")
        media.normalize(src, norm, canvas, fps=fps, crf=crf, log=log)
        ninfo = media.probe(norm)
        duration = ninfo.duration or info.duration or 60.0

        # 3. AI analysis (behind the scenes) — word timing for smarter cuts.
        job.set(stage="วิเคราะห์ด้วย AI", pct=base_pct + 8,
                message="🤖 AI กำลังวิเคราะห์เสียง…")
        tr = None
        try:
            tr = transcribe.transcribe(norm, language=LANGUAGE,
                                       model_size=WHISPER_MODEL, log=log)
            log(f"🤖 จับคำได้ {len(tr.words)} คำ / {len(tr.segments)} ช่วง")
        except transcribe.TranscribeUnavailable as e:
            log(f"⚠️ AI ใช้ไม่ได้ ({e}) — ใช้การตรวจช่วงเงียบแทน")

        # 4. Choose the parts to keep --------------------------------------
        job.set(stage="เลือกช่วงที่เหมาะสม", pct=base_pct + 13,
                message="🔎 เลือกช่วงที่มีคนพูด…")
        if tr and tr.segments:
            clips = analyze.select_for_review(tr, max_duration, variation=opts["variation"])
        else:
            clips = analyze.speech_zones(norm, duration, max_duration,
                                         variation=opts["variation"], log=log)
        if not clips:
            clips = [analyze.Clip(0.0, min(duration, max_duration))]
        log(f"✅ เลือก {len(clips)} ช่วง รวม {sum(c.duration for c in clips):.1f}s")

        # 4b. Tighten: drop dead air — word-precise (AI) so words aren't clipped.
        if opts["dead_air_on"]:
            job.set(stage="ตัดช่วงเงียบ (Dead Air)", pct=base_pct + 17,
                    message="🤫 ตัดช่วงเงียบออก…")
            clips = analyze.trim_dead_air(clips, transcript=tr, path=norm,
                                          aggressiveness=opts["dead_air_aggr"], log=log)

        # 5. Cut -------------------------------------------------------------
        job.set(stage="ตัดคลิป", pct=base_pct + 20, message="✂️ กำลังตัดคลิป…")
        cut_paths = editor.cut_clips(norm, clips, work_dir,
                                     prefix=f"v{idx:02d}", fmt=fmt, crf=crf, log=log)
        if not cut_paths:
            raise tools.ToolError(f"ตัดคลิปไม่สำเร็จสำหรับ {tag}")
        all_clips.extend(cut_paths)

    # 6. Build the final video file(s) --------------------------------------
    if opts["output_mode"] == "merged":
        videos = [_build_merged(job, all_clips, opts, fmt, max_duration)]
    else:
        videos = all_clips

    # 7. Save into a tidy, named project folder (+ optional MP3 folder) ------
    return _finalize(job, videos, opts, fmt)


def _build_merged(job, clips, opts, fmt, max_duration) -> str:
    """Merge all cut clips into one video file (in the work dir). Returns its path."""
    job.set(stage="รวมไฟล์", pct=90, message="🎬 รวมคลิปเป็นไฟล์เดียว…")
    merged = os.path.join(job.work_dir, f"merged.{fmt}")
    overlap = opts["lj_overlap"] if opts.get("lj_cut_on") else 0.0
    if overlap > 0 and len(clips) >= 2:
        job.set(message=f"🎞 เปลี่ยนฉากแบบมืออาชีพ ({opts['lj_cut_mode'].upper()}-cut {overlap:.2f}s)")
    editor.concat(clips, merged, fmt=fmt, max_duration=max_duration,
                  audio_overlap=overlap, lj_mode=opts["lj_cut_mode"],
                  crf=opts["crf"], log=job.log)

    if opts["music_path"]:
        job.set(stage="ใส่เพลงประกอบ", pct=95, message="🎵 ผสมเพลงประกอบ…")
        with_music = os.path.join(job.work_dir, f"merged_music.{fmt}")
        merged = editor.add_music(merged, opts["music_path"], with_music,
                                  fmt=fmt, max_duration=max_duration,
                                  music_volume=opts["music_volume"],
                                  crf=opts["crf"], log=job.log)
    return merged


def _finalize(job, videos: list[str], opts: dict, fmt: str) -> dict:
    """Save the deliverable video(s) into a tidy, named project folder.

    Layout (everything human-readable, nothing buried in a temp dir)::

        AutoCut Output/<ชื่อ> <DD-MM-YYYY HHMM>/
            Video/   <ชื่อ>.mp4   หรือ   <ชื่อ> 01.mp4, <ชื่อ> 02.mp4 …
            mp3/     <ชื่อ>.mp3 …                     (เฉพาะเมื่อเลือกแยกเสียง)
    """
    base = _project_base(opts.get("project_name"))
    proj_dir = storage.new_project_dir(base)
    proj_name = os.path.basename(proj_dir)
    video_dir = os.path.join(proj_dir, "Video")
    os.makedirs(video_dir, exist_ok=True)
    single = len(videos) == 1

    job.set(stage="บันทึกไฟล์", pct=94, message=f"💾 บันทึกลงโฟลเดอร์ “{proj_name}”…")
    for i, v in enumerate(videos, 1):
        name = f"{base}.{fmt}" if single else f"{base} {i:02d}.{fmt}"
        shutil.copy2(v, os.path.join(video_dir, name))

    if opts.get("audio_extract"):
        job.set(stage="แยกไฟล์เสียง", pct=97, message="🎧 แยกไฟล์เสียงเป็น MP3…")
        mp3_dir = os.path.join(proj_dir, "mp3")
        os.makedirs(mp3_dir, exist_ok=True)
        for i, v in enumerate(videos, 1):
            stem = base if single else f"{base} {i:02d}"
            editor.extract_audio(v, os.path.join(mp3_dir, f"{stem}.mp3"), log=job.log)

    return {"output_dir": proj_dir, "name": proj_name, "count": len(videos)}


# ===========================================================================
#  Helpers
# ===========================================================================
def _safe_name(name: str | None) -> str:
    if not name:
        return ""
    return os.path.basename(name).replace("\\", "_").replace("/", "_")


def _project_base(name: str | None) -> str:
    """A clean, Windows-safe base name for the project folder & clip files.

    Strips characters illegal in filenames, collapses whitespace, caps the
    length, and falls back to ``"Project"`` when the customer leaves it blank.
    """
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (name or "")).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return name[:60] or "Project"


def _natural_key(path: str):
    """Sort key so 'clip2' < 'clip10' (numbers compared as numbers)."""
    name = os.path.basename(path).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _to_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _to_float(value, default, lo, hi):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _valid_format(fmt: str) -> str:
    return fmt if fmt in editor.FORMATS else "mp4"


def _valid_lj_mode(m: str) -> str:
    return "j" if str(m).lower().startswith("j") else "l"


def _valid_aggr(a: str) -> str:
    return a if a in ("gentle", "medium", "strong") else "medium"


# Output quality / size validators.
_CRF = {"high": 18, "medium": 24, "saver": 28}  # high = sharp, large (the only UI option now)


def _valid_aspect(a: str) -> str:
    return a if a in ("auto", "16:9", "9:16", "1:1") else "auto"


def _valid_resolution(r: str) -> str:
    return r if str(r).lower() in ("auto", "720", "1080", "4k") else "auto"


def _valid_fps(f: str) -> int:
    return int(f) if str(f) in ("24", "30", "60") else 30


# ===========================================================================
#  Run
# ===========================================================================
if __name__ == "__main__":
    st = tools.status()
    print("=" * 56)
    print("  AutoCut backend")
    print(f"  ffmpeg : {st.ffmpeg or 'NOT FOUND'}")
    print(f"  ai     : {'whisper' if transcribe.available() else 'silence-fallback'} ({WHISPER_MODEL})")
    print(f"  เปิดเว็บที่ >> http://localhost:5000 <<")
    print("=" * 56)
    app.run(host="0.0.0.0", port=5000, threaded=True)
