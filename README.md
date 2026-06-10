# AutoCut ✂️ — ตัดคลิปอัตโนมัติ

แอพเดสก์ท็อปตัดคลิปอัตโนมัติ — แค่ลากวิดีโอลงไป AI จะถอดเสียงภาษาไทย เลือกช่วงที่พูดเด่น ๆ
ตัดช่วงเงียบทิ้ง ใส่ซับไตเติลให้ แล้วบันทึกเป็นโฟลเดอร์พร้อมใช้ — **ทำงานออฟไลน์ 100%**
(ลูกค้าไม่ต้องลง Python / Node / อะไรทั้งสิ้น ทุกอย่างฝังมาในตัว)

---

## ✨ ความสามารถ
- รับวิดีโอ **ทุก codec / ทุกนามสกุล** (MP4, MOV, MKV, AVI, WEBM, HEVC, …) — แปลงเป็นมาตรฐานก่อนเสมอ จึงไม่ค่อย error
- **ถอดเสียงภาษาไทย** ด้วย faster-whisper (โมเดล `small` ฝังในตัว ~460MB, ออฟไลน์)
- **AI เลือกช่วงที่ดีที่สุด** ให้อัตโนมัติ
- **ตัด Dead Air** (ช่วงเงียบ/ไม่พูด) — เลือกระดับ เบา / ปานกลาง / เข้ม
- **Subtitle อัตโนมัติ** (ffmpeg/libass ฝังฟอนต์ไทย) 4 สไตล์: highlight / pill / neon / kinetic
- **เปลี่ยนฉากแบบมืออาชีพ L-cut / J-cut** (เฉพาะโหมดรวมไฟล์เดียว)
- ใส่ **เพลงประกอบ** (ลดเสียงเพลงอัตโนมัติให้ฟังเสียงพูดชัด)
- **แยกไฟล์เสียงเป็น MP3** ได้
- เลือกนามสกุลปลายทาง: **MP4 / MOV / AVI / WEBM**
- เลือกโหมด: **แยกเป็นหลายคลิป** หรือ **รวมเป็นไฟล์เดียว**
- **ธีมสว่าง/มืด** + **ภาษา ไทย/อังกฤษ** (สลับมุมขวาบน, ค่าเริ่มต้น: มืด/ไทย)
- จับเวลาประมวลผลแบบนับขึ้น

---

## 👤 สำหรับผู้ใช้ (ลูกค้า)
1. ติดตั้งครั้งเดียว: ดับเบิลคลิก **`AutoCutPro-Setup.exe`** (หรือแตก `AutoCutPro-portable.zip` แล้วเปิด `AutoCutPro.exe`)
2. ครั้งแรกต้อง **เปิดใช้งานด้วยคีย์**: ส่ง “รหัสเครื่อง (Machine ID)” ให้แอดมิน → รับคีย์กลับมาวาง (ทำครั้งเดียว จากนั้นใช้ได้ตลอดแบบออฟไลน์ — คีย์ผูกกับเครื่อง)
3. ลากวิดีโอ → ตั้งค่า → กด **“ตัดคลิปอัตโนมัติ”**
4. เสร็จแล้วระบบ **เด้ง File Explorer** ไปที่โฟลเดอร์ผลงานให้เลย

### 📁 ผลลัพธ์ถูกบันทึกเป็นโฟลเดอร์ (ไม่ใช่ดาวน์โหลด)
```
AutoCut Output/
└─ <ชื่อคลิป> <วัน-เดือน-ปี เวลา>/      ← ตั้งชื่อเองได้ เว้นว่าง = "Project"
   ├─ Video/
   │   ├─ <ชื่อ> 01.mp4
   │   └─ <ชื่อ> 02.mp4              (โหมดรวม = ไฟล์เดียว <ชื่อ>.mp4)
   └─ mp3/                            (เฉพาะเมื่อเลือกแยกไฟล์เสียง)
       └─ <ชื่อ> 01.mp3
```

---

## 🔄 อัปเดตอัตโนมัติ (OTA)
แอพเช็คเวอร์ชันใหม่จาก GitHub เอง — เจอแล้วขึ้นปุ่ม **“🔔 มีอัปเดต”** กดแล้วปิด-เปิดใหม่ก็ได้ของใหม่
โหลดเฉพาะ **โค้ดที่เปลี่ยน** (ไม่กี่ KB) ไม่ต้องโหลดโมเดล 460MB ใหม่ และตรวจ **ลายเซ็น Ed25519**
ก่อนติดตั้งทุกครั้ง (กัน patch ปลอม) ถ้าแพตช์เสียจะถอยกลับโค้ดเดิมอัตโนมัติ

- แหล่งอัปเดต: `https://raw.githubusercontent.com/Panuwit2005/AutoCutProject/main/update`

---

## 🛠️ สำหรับ Dev — build แอพ
ต้องมี: **Python 3.11**, **ffmpeg** (`winget install Gyan.FFmpeg`), และ (ถ้าจะทำ installer) **Inno Setup** (`winget install JRSoftware.InnoSetup`)

```powershell
# แอพลูกค้า → release\AutoCutPro-Setup.exe + release\AutoCutPro-portable.zip
powershell -ExecutionPolicy Bypass -File "packaging\build.ps1"

# แอพ Admin → release\admin\AutoCutKeygen.exe  (รัน build.ps1 ก่อนหนึ่งครั้ง)
powershell -ExecutionPolicy Bypass -File "packaging\build_keygen.ps1"
```
สคริปต์จัดการ venv 3.11 + ลง deps + ก๊อป ffmpeg/โมเดล/ฟอนต์ + PyInstaller + zip + installer ให้ครบในที

> รันจาก source (ตอนพัฒนา): `py -3.11 -m venv .venv` → `pip install -r requirements.txt` → `python app.py` → เปิด http://localhost:5000

---

## 🚀 ปล่อยอัปเดต (Admin)
1. **Dev:** แก้โค้ด → push ขึ้น GitHub
2. **Admin:** `git pull` → เปิด **AutoCutKeygen.exe → แท็บ “เผยแพร่อัปเดต”** → ใส่เวอร์ชันใหม่ (เลขสูงขึ้นเสมอ เช่น 4.1) → กดสร้าง (ได้ `update/update.json` + `update/update-x.x.zip`)
3. push โฟลเดอร์ `update/` ขึ้น GitHub → ลูกค้าได้อัตโนมัติภายในไม่กี่นาที

ออก license key: แท็บ **“สร้างคีย์”** → วาง Machine ID ของลูกค้า → สร้าง → ส่งคีย์กลับ

---

## 🔐 ความปลอดภัย
- `admin_private_key.pem` เซ็นทั้ง **license** และ **อัปเดต** — เก็บไว้บน **เครื่อง Admin เครื่องเดียว**, ห้ามขึ้น GitHub (กันไว้ใน `.gitignore` แล้ว), และ **สำรองไว้ที่ปลอดภัย** — ถ้าหายจะออกคีย์/อัปเดตไม่ได้อีกเลย
- ในแอพลูกค้าฝังเฉพาะ **กุญแจตรวจสอบ (public)** — เปิดเผยได้ ปลอม/แชร์คีย์ไม่ได้
- repo ต้องเป็น **Public** เพื่อให้ลูกค้าโหลดอัปเดตผ่าน raw URL ได้ (ไฟล์ใหญ่ exe/โมเดลถูกกันไม่ให้ขึ้น GitHub อยู่แล้ว)

---

## 🧩 โครงสร้างโค้ด
```
app.py                Flask server + คุม pipeline ทั้งหมด (งานหนักรันใน background thread)
index.html            หน้า UI (ธีม + 2 ภาษา + progress + อัปเดต)  ·  static/style.css
autocut/
  tools.py            หา ffmpeg/ffprobe เอง + inject PATH
  media.py            probe + normalize ทุก codec ให้เป็นมาตรฐานเดียว
  transcribe.py       ถอดเสียง (faster-whisper → ตัดตามช่วงเงียบ ถ้าถอดไม่ได้)
  analyze.py          ให้คะแนน + เลือกช่วงที่ดีที่สุด + ตัด dead air
  editor.py           ตัด / ต่อ / ผสมเพลง / แยก MP3 (ffmpeg) + L/J-cut
  subtitles.py        เรนเดอร์ซับไตเติล (libass)
  storage.py          เลือกที่เก็บไฟล์ + สร้างโฟลเดอร์ผลงาน (AutoCut Output)
  licensing.py        เปิดใช้งานแบบผูกเครื่อง (Ed25519)
  updater.py          ระบบอัปเดต OTA (เช็ค/โหลด/ตรวจลายเซ็น/แตกไฟล์)
  folder_picker.py    หน้าต่างเลือกโฟลเดอร์ของ Windows
packaging/
  launcher.py         entry point ของ .exe (โหลด overlay อัปเดต + เปิดหน้าต่าง)
  keygen_gui.py       แอพ Admin (สร้างคีย์ + เผยแพร่อัปเดต)
  build.ps1 / build_keygen.ps1 / *.spec / installer.iss
```

---

## ⚙️ เวอร์ชัน/เทคโนโลยี
Python 3.11.9 · faster-whisper 1.1.0 · ctranslate2 · ffmpeg 8.1.1 (static) · PyInstaller 6.11.1 · waitress · pywebview · cryptography (Ed25519)
