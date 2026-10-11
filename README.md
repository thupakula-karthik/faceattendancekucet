# Hardware-Free BYOD Attendance Platform (Pure Face & Classroom Geofences)

A cloud-deployable web attendance platform designed for Bring Your Own Device (BYOD) operation on student mobile browsers. Eliminates TOTP codes in favor of **Pure Facial Biometric Verification** tied to **Classroom-Level Geofences** and **Administrative Relocation Approvals**.

---

## 1. Core Operating Principles

### A. Pure Face Attendance (Zero PIN/TOTP Codes)
- Students do not need to memorize or enter rotating numeric codes.
- Attendance check-in is accomplished with a **single-tap Face Scan** directly from the student's mobile browser.
- Uses **1:1 Euclidean facial vector matching** ($D \le 0.44$) against the student's enrolled 128-dimensional template, backed by multi-frame optical micro-movement and screen spoof filters.

### B. Subject-Wise Attendance at Required Class Timetable Slots
- Attendance sessions represent specific **Subject Lectures** (e.g., *Distributed Systems CS301* for *3rd Year CS-A*).
- Sessions are bound to scheduled timetable slots (e.g. *09:30 AM – 10:20 AM*).
- Only students enrolled in that specific department, year, and class section can check into the active subject lecture.

### C. Individual Classroom-Level Geofencing
- Every classroom, laboratory, and seminar hall has its own distinct physical GPS coordinates and a tight room perimeter ($\sim 25\text{–}30\text{m}$).
- The Haversine formula strictly verifies that the student is physically seated inside that specific classroom, rejecting students attempting check-in from hallways, hostels, or canteens.

### D. Teacher Geofence Relocation with Mandatory Admin Approval
- If a teacher needs to relocate a class (e.g. shifting to a computer lab or seminar hall due to hardware issues), the teacher submits a **Classroom Relocation Request** through their dashboard.
- The new geofence **remains inactive** until the **Head of Management (Institutional Admin)** reviews the justification and officially **Approves** the request.
- Once approved, the active session automatically shifts its boundary to the new classroom, with confirmation dispatched via SMS.

---

## 2. Four-Tier Role Matrix

| Role | Portal URL | Primary Responsibilities |
| :--- | :--- | :--- |
| **Head of Management** | `/management` | Approve/Reject classroom geofence change requests, manage room coordinates, assign students to faculty, and view SMS gateway logs. |
| **Teacher** | `/teacher` | Launch subject-wise class sessions at scheduled times, submit classroom relocation requests for admin approval, and execute manual overrides for device failures. |
| **Student (BYOD)** | `/student` | Pure face scan check-in within classroom geofence, webcam self-enrollment, and AI attendance trajectory tracking. |
| **Parent** | `/parent` | Ward presence tracking, 5-day continuous absence alerts, and monthly attendance reports dispatchable to mobile. |

---

## 3. Demo Credentials

| Role | Username / Reg No | Password | Allocated Class / Scope |
| :--- | :--- | :--- | :--- |
| **Head of Management** | `admin` | `admin123` | Institutional Admin |
| **Teacher (CSE)** | `teacher1` | `teacher123` | 3rd Year (Class: `CS-A`) |
| **Student** | `student1` (*or `REG2026-CS101`*) | `student123` | 3rd Year, `CS-A` |
| **Parent** | `parent1` | `parent123` | Guardian of Alex Mercer |

---

## 4. Execution & Cloud Deployment

### Local Execution:
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Navigate to `http://localhost:8000` on any mobile or desktop browser.

### Cloud Deployment (Render & Netlify):
- **Backend:** Deploy as a Python Web Service on Render using the included `render.yaml` or `Dockerfile`.
- **Frontend:** Serve directly via FastAPI with HTTPS enabled (mandatory for WebRTC camera & Geolocation APIs).
