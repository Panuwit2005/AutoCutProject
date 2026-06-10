# AutoCut Pro 🎬✂️

เว็บตัดต่อวิดีโอรีวิวสินค้าอัตโนมัติ — ลูกค้าแค่ลากไฟล์วิดีโอลงไป AI จะถอดเสียง
เลือกช่วงที่พูดถึงสินค้า/คุณภาพ ตัดส่วนเงียบทิ้ง แล้วได้คลิปสั้นพร้อม subtitle

## ใช้ทำอะไรได้
- รับวิดีโอ **ทุก codec / ทุกนามสกุล** (MP4, MOV, MKV, AVI, WEBM, HEVC, …) — แปลงให้เป็นมาตรฐานก่อนเสมอ จึงไม่ค่อย error
- **ถอดเสียงภาษาไทย** ด้วย faster-whisper (Whisper)
- **AI เลือกช่วงที่ดีที่สุด** จากคำสำคัญรีวิว (แนะนำ, คุณภาพ, คุ้มค่า ฯลฯ)
- **ตัด Dead Air อัตโนมัติ** — ตัดช่วงที่ไม่มีคนพูดออก โดยใช้จังหวะคำพูดจาก AI (ทนเสียงเพลงประกอบดัง) เลือกระดับ เบา/ปานกลาง/เข้ม ได้
- **Subtitle อัตโนมัติด้วย HyperFrames** (4 สไตล์: highlight / pill / neon / kinetic)
- ออกได้ทั้ง **ZIP คลิปแยกไฟล์** (เข้า CapCut ต่อได้) หรือ **รวมไฟล์เดียว** + ใส่เพลงประกอบ
- **เปลี่ยนฉากแบบมืออาชีพ (L-cut / J-cut)** — เสียงคาบเกี่ยวรอยต่อระหว่างฉาก (เฉพาะโหมดรวมไฟล์เดียว)
- เลือกนามสกุลปลายทาง: MP4 / MOV / AVI / WEBM

## ความต้องการของเครื่อง (ติดตั้งครั้งเดียว)
| สิ่งที่ต้องมี | ติดตั้งด้วย |
|---|---|
| Python 3.11 (+ venv) | `py -3.11 -m venv .venv` |
| Python deps | `.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| ffmpeg + ffprobe | `winget install Gyan.FFmpeg` |
| Node.js + npx | https://nodejs.org |
| Chrome (สำหรับ subtitle) | `npx hyperframes browser ensure` |

> ✅ บนเครื่องนี้ติดตั้งครบแล้ว — แค่ดับเบิลคลิก `start.bat`

## วิธีเปิดใช้งาน
1. ดับเบิลคลิก **`start.bat`** (หรือ `\.venv\Scripts\python.exe app.py`)
2. เปิดเบราว์เซอร์ไปที่ **http://localhost:5000**
3. ลากวิดีโอลงไป → ตั้งค่า → กด “ตัดต่อวิดีโออัตโนมัติ”

ตรวจสถานะเครื่องมือ: เปิด http://localhost:5000/debug

## โครงสร้างโค้ด
```
app.py              Flask server + คุมงานทั้ง pipeline (งานหนักรันใน background thread)
autocut/
  tools.py          หา ffmpeg/ffprobe/npx เอง + inject PATH (กัน "ffmpeg not found")
  media.py          probe + normalize ทุก codec ให้เป็นมาตรฐานเดียว
  transcribe.py     ถอดเสียง (faster-whisper → hyperframes → ตัดตามช่วงเงียบ)
  analyze.py        ให้คะแนน+เลือกช่วงรีวิวที่ดีที่สุด
  editor.py         ตัด / ต่อ / ผสมเพลง (ffmpeg)
  subtitles.py      สร้าง composition + render ผ่าน HyperFrames + composite
index.html          หน้าเว็บ (อัปโหลด + progress จริง)
static/style.css    สไตล์
```

## ตั้งค่าเพิ่มเติม (ตัวเลือก)
ตั้งค่าผ่าน environment variable ก่อนรัน:
- `AUTOCUT_WHISPER_MODEL` — `tiny` / `base` / `small` (ค่าเริ่มต้น) / `medium` / `large-v3`
  ภาษาไทยเสียงมีเพลงดัง แนะนำ `medium` เพื่อความแม่นยำ (ช้าลงแต่ดีขึ้น)
- `AUTOCUT_LANGUAGE` — ค่าเริ่มต้น `th`
- `AUTOCUT_FFMPEG` / `AUTOCUT_FFPROBE` — ระบุ path เองถ้าหาไม่เจอ

## หมายเหตุ
- ถ้าไม่มี Chrome/HyperFrames หรือ render subtitle พลาด ระบบจะส่งคลิปที่ตัดแล้ว **โดยไม่มี subtitle** (ไม่ล้มทั้งงาน)
- **L-cut / J-cut**: ใช้ได้แล้วในโหมดรวมไฟล์เดียว (เปิดสวิตช์ "การเปลี่ยนฉากแบบมืออาชีพ") — ถ้าสร้าง graph ไม่สำเร็จจะถอยไปต่อแบบปกติเองโดยไม่ล้มงาน
- Sound Effect: โครงสร้างเตรียมไว้แล้ว อยู่ระหว่างพัฒนาเฟสถัดไป
```
