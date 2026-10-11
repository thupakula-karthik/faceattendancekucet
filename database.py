import sqlite3
import os
import json
import base64
import random
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional

def get_db_path() -> str:
    env_db = os.environ.get("DB_FILE")
    if env_db:
        return env_db
    local_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attendance.db")
    try:
        # Test if current directory supports standard file locking (overlayfs, ext4, ntfs, etc.)
        test_conn = sqlite3.connect(local_db, timeout=2.0)
        test_conn.execute("CREATE TABLE IF NOT EXISTS _test_lock (id INT)")
        test_conn.execute("INSERT OR REPLACE INTO _test_lock VALUES (1)")
        test_conn.commit()
        test_conn.close()
        return local_db
    except Exception:
        # Fallback to temp directory if filesystem lacks POSIX advisory lock support (e.g. 9p, NFS)
        import tempfile
        return os.path.join(tempfile.gettempdir(), "attendance.db")

DB_FILE = get_db_path()

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=60.0, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 60000;")
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn

def get_setting(key: str, default: str = "") -> str:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default

def set_setting(key: str, value: str):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value.strip()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SETTING ERROR]: {e}")

def generate_messaging_links(phone: str, message: str) -> dict:
    """
    Generates 100% FREE direct 1-tap WhatsApp and native mobile SMS links.
    Zero API keys, zero DLT registration, zero carrier fees.
    """
    clean_phone = ''.join(c for c in str(phone or '') if c.isdigit())
    
    # Prepend India country code (91) if 10-digit number for WhatsApp
    wa_phone = clean_phone
    if len(clean_phone) == 10:
        wa_phone = f"91{clean_phone}"
    elif clean_phone.startswith('0') and len(clean_phone) == 11:
        wa_phone = f"91{clean_phone[1:]}"

    encoded_msg = urllib.parse.quote(message)
    whatsapp_url = f"https://wa.me/{wa_phone}?text={encoded_msg}" if wa_phone else ""
    sms_url = f"sms:{clean_phone}?body={encoded_msg}" if clean_phone else ""

    return {
        "whatsapp_url": whatsapp_url,
        "sms_url": sms_url,
        "clean_phone": clean_phone,
        "wa_phone": wa_phone,
        "encoded_message": encoded_msg,
        "raw_message": message
    }

def dispatch_carrier_sms(phone: str, message: str) -> bool:
    """
    Attempts free server-side notifications (Telegram Bot, Free Webhook)
    or optional legacy carrier gateways (Fast2SMS / Twilio) if configured.
    """
    clean_phone = ''.join(c for c in str(phone or '') if c.isdigit())

    # 1. Free Telegram Bot Gateway (100% Free Forever, Unlimited, Zero DLT/Carrier Blocks)
    telegram_token = get_setting("TELEGRAM_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    telegram_chat = get_setting("TELEGRAM_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    if telegram_token and telegram_chat:
        try:
            tg_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            tg_payload = json.dumps({
                "chat_id": telegram_chat,
                "text": f"📢 <b>Institutional Notification ({clean_phone})</b>\n\n{message}",
                "parse_mode": "HTML"
            }).encode('utf-8')
            req = urllib.request.Request(tg_url, data=tg_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                print(f"[TELEGRAM FREE DISPATCH SUCCESS]: {resp.status}")
                return True
        except Exception as e:
            print(f"[TELEGRAM DISPATCH ERROR]: {e}")

    # 2. Free Webhook Gateway (Discord / Slack / CallMeBot / Custom Webhook)
    webhook_url = get_setting("FREE_WEBHOOK_URL", os.environ.get("FREE_WEBHOOK_URL", "")).strip()
    if webhook_url:
        try:
            wh_payload = json.dumps({
                "phone": clean_phone,
                "message": message,
                "content": f"📱 **Notification for {clean_phone}**\n{message}"
            }).encode('utf-8')
            req = urllib.request.Request(webhook_url, data=wh_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                print(f"[FREE WEBHOOK DISPATCH SUCCESS]: {resp.status}")
                return True
        except Exception as e:
            print(f"[FREE WEBHOOK DISPATCH ERROR]: {e}")

    # 3. Fast2SMS Provider (Optional, if key exists)
    if clean_phone.startswith('91') and len(clean_phone) == 12:
        fast_phone = clean_phone[2:]
    else:
        fast_phone = clean_phone

    fast2sms_key = get_setting("FAST2SMS_API_KEY", os.environ.get("FAST2SMS_API_KEY", "")).strip()
    if fast2sms_key:
        try:
            url = "https://www.fast2sms.com/dev/bulkV2"
            payload = json.dumps({
                "route": "q",
                "message": message,
                "language": "english",
                "flash": 0,
                "numbers": fast_phone
            }).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={
                "authorization": fast2sms_key,
                "Content-Type": "application/json"
            })
            with urllib.request.urlopen(req, timeout=3) as response:
                print(f"[FAST2SMS LIVE DISPATCH] Status: {response.status}")
                return True
        except Exception as e:
            print(f"[FAST2SMS GATEWAY ERROR]: {e}")

    # 4. Twilio Provider (Optional, if keys exist)
    twilio_sid = get_setting("TWILIO_ACCOUNT_SID", os.environ.get("TWILIO_ACCOUNT_SID", "")).strip()
    twilio_token = get_setting("TWILIO_AUTH_TOKEN", os.environ.get("TWILIO_AUTH_TOKEN", "")).strip()
    twilio_from = get_setting("TWILIO_PHONE_NUMBER", os.environ.get("TWILIO_PHONE_NUMBER", "")).strip()
    if twilio_sid and twilio_token and twilio_from:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            to_number = phone if phone.startswith('+') else f"+91{fast_phone}"
            data = urllib.parse.urlencode({
                "To": to_number,
                "From": twilio_from,
                "Body": message
            }).encode('utf-8')
            auth_str = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode('utf-8')).decode('utf-8').strip()
            req = urllib.request.Request(url, data=data, headers={
                "Authorization": f"Basic {auth_str}",
                "Content-Type": "application/x-www-form-urlencoded"
            })
            with urllib.request.urlopen(req, timeout=3) as response:
                print(f"[TWILIO LIVE DISPATCH] Status: {response.status}")
                return True
        except Exception as e:
            print(f"[TWILIO GATEWAY ERROR]: {e}")

    return False

def send_sms(recipient_phone: str, recipient_name: str, recipient_role: str, message_type: str, message_body: str) -> dict:
    """
    Orchestrates messaging transmission:
    1. Attempts free delivery (Telegram/Webhook) or carrier if configured
    2. Logs record in database with pre-generated 1-tap WhatsApp and SMS links
    """
    carrier_dispatched = False
    try:
        carrier_dispatched = dispatch_carrier_sms(recipient_phone, message_body)
    except Exception as e:
        print(f"[SMS DISPATCH EXCEPTION]: {e}")

    status_label = "DELIVERED_SERVER" if carrier_dispatched else "READY_FOR_SINGLE_TAP_SEND"

    conn = None
    log_id = 0
    try:
        conn = get_db()
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""INSERT INTO sms_logs 
                          (recipient_phone, recipient_name, recipient_role, message_type, message_body, dispatched_at, status)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (recipient_phone, recipient_name, recipient_role, message_type, message_body, now_str, status_label))
        conn.commit()
        log_id = cursor.lastrowid
        print(f"[SMS LOG] {recipient_phone} ({recipient_name}): {message_body[:60]}... [Status: {status_label}]")
    except Exception as e:
        print(f"[SMS LOG ERROR]: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

    links = generate_messaging_links(recipient_phone, message_body)
    return {
        "id": log_id,
        "status": status_label,
        "recipient_phone": recipient_phone,
        "recipient_name": recipient_name,
        "recipient_role": recipient_role,
        "message_type": message_type,
        "message_body": message_body,
        **links
    }

def get_monthly_attendance_shortage(month_str: Optional[str] = None,
                                    department: Optional[str] = None,
                                    year: Optional[str] = None,
                                    class_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Identifies all students whose attendance is strictly below 75% for the specified month.
    Generates pre-formatted 1-tap WhatsApp and SMS links for both parents and students.
    """
    if not month_str or not month_str.strip():
        month_str = datetime.now().strftime("%Y-%m")
    else:
        month_str = month_str.strip()[:7]

    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        month_display_name = dt.strftime("%B %Y")
    except Exception:
        month_display_name = month_str

    conn = get_db()
    cursor = conn.cursor()

    query = """SELECT id, name, reg_no, username, department, year, class_name, phone, parent_phone, is_face_enrolled 
               FROM users WHERE role='student'"""
    params = []
    if department and department.strip() and department.strip() != "ALL":
        query += " AND department = ?"
        params.append(department.strip())
    if year and year.strip() and year.strip() != "ALL":
        query += " AND year = ?"
        params.append(year.strip())
    if class_name and class_name.strip() and class_name.strip() != "ALL":
        query += " AND class_name = ?"
        params.append(class_name.strip())
    query += " ORDER BY department ASC, year ASC, name ASC"
    cursor.execute(query, params)
    students = [dict(r) for r in cursor.fetchall()]

    # Total distinct sessions or attendance dates in this month
    cursor.execute("""SELECT id, class_name, department, year, date(scheduled_start) as s_date 
                      FROM sessions 
                      WHERE strftime('%Y-%m', scheduled_start) = ? OR strftime('%Y-%m', created_at) = ?""",
                   (month_str, month_str))
    month_sessions = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT date FROM attendance WHERE strftime('%Y-%m', date) = ?", (month_str,))
    att_dates = [r[0] for r in cursor.fetchall()]

    all_month_dates = sorted(list(set([s["s_date"] for s in month_sessions if s.get("s_date")] + att_dates)))
    total_college_working_days = max(len(all_month_dates), 1 if (month_sessions or att_dates) else 0)

    # All attendance records for this month
    cursor.execute("""SELECT student_id, session_id, date 
                      FROM attendance 
                      WHERE strftime('%Y-%m', date) = ?""", (month_str,))
    month_attendance = cursor.fetchall()
    conn.close()

    student_attended_dates = {}
    for r in month_attendance:
        sid = r["student_id"]
        d = r["date"]
        if sid not in student_attended_dates:
            student_attended_dates[sid] = set()
        student_attended_dates[sid].add(d)

    shortage_students = []
    satisfactory_students = []

    for s in students:
        sid = s["id"]
        # Class specific dates if defined
        class_dates = set([sess["s_date"] for sess in month_sessions 
                           if sess.get("s_date") and (not sess.get("class_name") or sess["class_name"] == s.get("class_name") or sess["department"] == s.get("department"))])
        
        total_days = len(class_dates) if len(class_dates) > 0 else total_college_working_days
        if total_days == 0:
            total_days = max(len(att_dates), 1)

        attended_days = len(student_attended_dates.get(sid, set()))
        pct = round((attended_days / float(total_days)) * 100.0, 1) if total_days > 0 else 100.0

        parent_phone = s.get("parent_phone") or ""
        student_phone = s.get("phone") or ""
        reg_id = s.get("reg_no") or s.get("username") or "STUDENT"

        parent_msg = (
            f"🚨 ATTENDANCE SHORTAGE NOTICE: Dear Parent, your ward {s['name']} ({reg_id}) "
            f"has secured only {pct}% attendance ({attended_days}/{total_days} working days) "
            f"in {month_display_name}. Minimum mandatory attendance is 75% as per university regulations. "
            f"Falling below 75% will lead to semester exam debarment. "
            f"Please contact the Head of Department immediately."
        )

        student_msg = (
            f"⚠️ ACADEMIC SHORTAGE WARNING: Dear {s['name']} ({reg_id}), your monthly attendance for "
            f"{month_display_name} is currently {pct}% ({attended_days}/{total_days} days). "
            f"You have fallen below the mandatory 75% threshold. "
            f"Regularize your attendance immediately to avoid academic disciplinary penalties."
        )

        parent_links = generate_messaging_links(parent_phone, parent_msg)
        student_links = generate_messaging_links(student_phone, student_msg)

        item = {
            **s,
            "total_working_days": total_days,
            "attended_days": attended_days,
            "attendance_pct": pct,
            "is_shortage": (pct < 75.0),
            "parent_msg": parent_msg,
            "student_msg": student_msg,
            "parent_whatsapp_url": parent_links["whatsapp_url"],
            "parent_sms_url": parent_links["sms_url"],
            "student_whatsapp_url": student_links["whatsapp_url"],
            "student_sms_url": student_links["sms_url"]
        }

        if pct < 75.0:
            shortage_students.append(item)
        else:
            satisfactory_students.append(item)

    return {
        "month": month_str,
        "month_display_name": month_display_name,
        "total_college_working_days": total_college_working_days,
        "total_students_evaluated": len(students),
        "shortage_count": len(shortage_students),
        "satisfactory_count": len(satisfactory_students),
        "shortage_students": shortage_students,
        "satisfactory_students": satisfactory_students
    }

def get_consecutive_absentees(consecutive_days: int = 4,
                              department: Optional[str] = None,
                              year: Optional[str] = None,
                              class_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Identifies all students continuously absent for consecutive_days (default 4 days).
    Generates pre-formatted 1-tap WhatsApp and SMS links for both parents and students.
    """
    conn = get_db()
    cursor = conn.cursor()

    query = """SELECT id, name, reg_no, username, department, year, class_name, phone, parent_phone, is_face_enrolled 
               FROM users WHERE role='student'"""
    params = []
    if department and department.strip() and department.strip() != "ALL":
        query += " AND department = ?"
        params.append(department.strip())
    if year and year.strip() and year.strip() != "ALL":
        query += " AND year = ?"
        params.append(year.strip())
    if class_name and class_name.strip() and class_name.strip() != "ALL":
        query += " AND class_name = ?"
        params.append(class_name.strip())
    query += " ORDER BY department ASC, year ASC, name ASC"
    cursor.execute(query, params)
    students = [dict(r) for r in cursor.fetchall()]

    # Find distinct session and attendance dates, sorted newest to oldest
    cursor.execute("SELECT DISTINCT date(scheduled_start) FROM sessions ORDER BY date(scheduled_start) DESC")
    session_dates = [r[0] for r in cursor.fetchall() if r[0]]
    cursor.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    att_dates = [r[0] for r in cursor.fetchall() if r[0]]

    all_academic_dates = sorted(list(set(session_dates + att_dates)), reverse=True)

    cursor.execute("SELECT student_id, date FROM attendance")
    att_records = cursor.fetchall()
    conn.close()

    student_attendance_by_date = {}
    for r in att_records:
        sid = r["student_id"]
        d = r["date"]
        if sid not in student_attendance_by_date:
            student_attendance_by_date[sid] = set()
        student_attendance_by_date[sid].add(d)

    consecutive_absentees = []

    for s in students:
        sid = s["id"]
        attended_dates = student_attendance_by_date.get(sid, set())
        
        consecutive_count = 0
        consecutive_dates = []
        for d in all_academic_dates:
            if d not in attended_dates:
                consecutive_count += 1
                consecutive_dates.append(d)
            else:
                break
        
        if consecutive_count >= consecutive_days:
            formatted_dates = []
            for d in consecutive_dates[:consecutive_count]:
                try:
                    f_d = datetime.strptime(d, "%Y-%m-%d").strftime("%d %b")
                except Exception:
                    f_d = d
                formatted_dates.append(f_d)
            dates_str = ", ".join(formatted_dates)

            parent_phone = s.get("parent_phone") or ""
            student_phone = s.get("phone") or ""
            reg_id = s.get("reg_no") or s.get("username") or "STUDENT"

            parent_msg = (
                f"🚨 CRITICAL ALERT - 4 DAYS CONTINUOUS ABSENCE: "
                f"Dear Parent, your ward {s['name']} ({reg_id}) has been continuously ABSENT "
                f"for {consecutive_count} CONSECUTIVE DAYS ({dates_str}). "
                f"As per institutional regulations, continuous 4-day unauthorized absence requires an immediate explanation. "
                f"Please contact the college principal/HOD office immediately."
            )

            student_msg = (
                f"🚨 URGENT WARNING - 4 DAYS CONTINUOUS ABSENCE: "
                f"{s['name']} ({reg_id}), you have been marked absent for {consecutive_count} consecutive days ({dates_str}). "
                f"Report to the HOD/Dean office tomorrow morning with parent/medical justification to avoid disciplinary action."
            )

            parent_links = generate_messaging_links(parent_phone, parent_msg)
            student_links = generate_messaging_links(student_phone, student_msg)

            consecutive_absentees.append({
                **s,
                "consecutive_absent_days": consecutive_count,
                "absent_dates": consecutive_dates[:consecutive_count],
                "absent_dates_str": dates_str,
                "parent_msg": parent_msg,
                "student_msg": student_msg,
                "parent_whatsapp_url": parent_links["whatsapp_url"],
                "parent_sms_url": parent_links["sms_url"],
                "student_whatsapp_url": student_links["whatsapp_url"],
                "student_sms_url": student_links["sms_url"]
            })

    return {
        "required_consecutive_days": consecutive_days,
        "academic_dates_tracked": all_academic_dates[:10],
        "consecutive_absent_count": len(consecutive_absentees),
        "consecutive_absentees": consecutive_absentees
    }
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Users Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL,
                        name TEXT NOT NULL,
                        email TEXT,
                        phone TEXT,
                        parent_phone TEXT,
                        reg_no TEXT UNIQUE,
                        department TEXT,
                        year TEXT,
                        class_name TEXT,
                        assigned_teacher_id INTEGER,
                        student_id INTEGER,
                        is_face_enrolled INTEGER DEFAULT 0,
                        face_embedding TEXT,
                        face_photo TEXT,
                        created_at TEXT)''')
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN face_embedding TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN face_photo TEXT")
    except Exception:
        pass

    # 2. Venues / Classrooms Table (Room-Level Geofences: 20-30m per room)
    cursor.execute('''CREATE TABLE IF NOT EXISTS venues (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        building TEXT NOT NULL,
                        room_no TEXT NOT NULL,
                        latitude REAL NOT NULL,
                        longitude REAL NOT NULL,
                        radius_meters REAL NOT NULL DEFAULT 30.0)''')

    # 3. Subject-Wise Class Scheduled Sessions Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_code TEXT UNIQUE NOT NULL,
                        teacher_id INTEGER NOT NULL,
                        teacher_name TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        department TEXT NOT NULL,
                        year TEXT NOT NULL,
                        class_name TEXT NOT NULL,
                        venue_name TEXT NOT NULL,
                        latitude REAL NOT NULL,
                        longitude REAL NOT NULL,
                        radius_meters REAL NOT NULL DEFAULT 30.0,
                        scheduled_start TEXT NOT NULL,
                        scheduled_end TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        geofence_status TEXT NOT NULL DEFAULT 'APPROVED')''')

    # 4. Geofence Location Change Requests (Requires Admin Approval)
    cursor.execute('''CREATE TABLE IF NOT EXISTS geofence_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER,
                        teacher_id INTEGER NOT NULL,
                        teacher_name TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        class_name TEXT NOT NULL,
                        old_venue_name TEXT,
                        new_venue_name TEXT NOT NULL,
                        new_latitude REAL NOT NULL,
                        new_longitude REAL NOT NULL,
                        new_radius_meters REAL NOT NULL DEFAULT 30.0,
                        reason TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        requested_at TEXT NOT NULL,
                        reviewed_by TEXT,
                        reviewed_at TEXT)''')

    # 5. Dual-Factor Attendance Transaction Log Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER NOT NULL,
                        student_id INTEGER NOT NULL,
                        student_name TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        date TEXT NOT NULL,
                        time TEXT NOT NULL,
                        distance_meters REAL,
                        face_distance REAL,
                        device_fingerprint TEXT,
                        status TEXT NOT NULL,
                        is_manual_override INTEGER DEFAULT 0,
                        override_by TEXT,
                        remarks TEXT)''')

    # 6. SMS Logs Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS sms_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        recipient_phone TEXT NOT NULL,
                        recipient_name TEXT NOT NULL,
                        recipient_role TEXT NOT NULL,
                        message_type TEXT NOT NULL,
                        message_body TEXT NOT NULL,
                        dispatched_at TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'SENT')''')

    # 7. Departments / Branches Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS departments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        code TEXT NOT NULL)''')

    # 8. Classes Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS classes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        department TEXT NOT NULL,
                        year TEXT NOT NULL)''')

    # 9. System & Gateway Settings Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT)''')

    conn.commit()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Seed Individual Classroom Geofences
    cursor.execute("SELECT COUNT(*) FROM venues")
    if cursor.fetchone()[0] == 0:
        default_venues = [
            ("Classroom 101 - CS Block", "CS Department Building", "Room 101", 17.385044, 78.486671, 25.0),
            ("Classroom 102 - CS Block", "CS Department Building", "Room 102", 17.385150, 78.486750, 25.0),
            ("Classroom 204 - EEE Wing", "Engineering Block B", "Room 204", 17.384210, 78.485300, 25.0),
            ("Advanced AI & Networks Lab 3", "Research Complex", "Lab 3", 17.386120, 78.487950, 30.0),
            ("Electronics Embedded Systems Lab", "Block C", "Lab 12", 17.384500, 78.485600, 25.0),
            ("Mechanical Workshop & CAD Lab", "Heavy Machinery Block", "Room 105", 17.383800, 78.484900, 30.0),
            ("Main University Seminar Hall", "Central Administrative Building", "Hall A", 17.385600, 78.486200, 35.0)
        ]
        cursor.executemany('''INSERT INTO venues (name, building, room_no, latitude, longitude, radius_meters) 
                              VALUES (?, ?, ?, ?, ?, ?)''', default_venues)
        conn.commit()

    # Seed Expanded Academic Branches / Departments (8 Major Disciplines)
    cursor.execute("SELECT COUNT(*) FROM departments")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT OR IGNORE INTO departments (name, code) VALUES (?, ?)", [
            ("Computer Science & Engineering", "CSE"),
            ("Electronics & Communication Engineering", "ECE"),
            ("Electrical & Electronics Engineering", "EEE"),
            ("Mechanical Engineering", "MECH"),
            ("Civil Engineering", "CIVIL"),
            ("Information Technology", "IT"),
            ("Artificial Intelligence & Data Science", "AI&DS"),
            ("Master of Business Administration", "MBA")
        ])
        conn.commit()

    # Seed Classes
    cursor.execute("SELECT COUNT(*) FROM classes")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO classes (name, department, year) VALUES (?, ?, ?)", [
            ("CS-A", "Computer Science & Engineering", "3rd Year"),
            ("CS-B", "Computer Science & Engineering", "3rd Year"),
            ("ECE-A", "Electronics & Communication Engineering", "3rd Year"),
            ("EE-A", "Electrical & Electronics Engineering", "2nd Year"),
            ("MECH-A", "Mechanical Engineering", "3rd Year"),
            ("CIVIL-A", "Civil Engineering", "2nd Year"),
            ("AIDS-A", "Artificial Intelligence & Data Science", "1st Year"),
            ("MBA-A", "Master of Business Administration", "1st Year")
        ])
        conn.commit()

    # Seed baseline users
    def insert_user_if_missing(username, password, role, name, email, phone, parent_phone=None,
                               reg_no=None, department=None, year=None, class_name=None,
                               assigned_teacher_id=None, student_id=None):
        cursor.execute("SELECT id FROM users WHERE username=?", (username,))
        if not cursor.fetchone():
            cursor.execute('''INSERT INTO users 
                              (username, password, role, name, email, phone, parent_phone, reg_no, department, year, class_name, assigned_teacher_id, student_id, created_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (username, password, role, name, email, phone, parent_phone, reg_no, department, year, class_name, assigned_teacher_id, student_id, now_str))
            conn.commit()

    # Admin user: STRICTLY for website and institutional administration (NO class, NO year, NO mentor)
    insert_user_if_missing('admin', 'admin123', 'management', 'Dr. Arthur Vance', 'vance@institution.edu', '+91-9800000001',
                           department='Central Administration', year=None, class_name=None, assigned_teacher_id=None)

    insert_user_if_missing('teacher1', 'teacher123', 'teacher', 'Prof. Sarah Connor', 'connor@institution.edu', '+91-9800000010',
                           department='Computer Science & Engineering', year='3rd Year', class_name='CS-A')
    cursor.execute("SELECT id FROM users WHERE username='teacher1'")
    teacher1_id = cursor.fetchone()[0]

    insert_user_if_missing('teacher2', 'teacher123', 'teacher', 'Dr. Alan Turing', 'turing@institution.edu', '+91-9800000020',
                           department='Electrical & Electronics Engineering', year='2nd Year', class_name='EE-A')

    insert_user_if_missing('student1', 'student123', 'student', 'Alex Mercer', 'alex.mercer@student.edu', '+91-9876543210',
                           parent_phone='+91-9876543299', reg_no='REG2026-CS101',
                           department='Computer Science & Engineering', year='3rd Year', class_name='CS-A',
                           assigned_teacher_id=teacher1_id)
    cursor.execute("SELECT id FROM users WHERE username='student1'")
    alex_id = cursor.fetchone()[0]

    insert_user_if_missing('student2', 'student123', 'student', 'Emily Blunt', 'emily.blunt@student.edu', '+91-9876543211',
                           parent_phone='+91-9876543298', reg_no='REG2026-CS102',
                           department='Computer Science & Engineering', year='3rd Year', class_name='CS-A',
                           assigned_teacher_id=teacher1_id)

    insert_user_if_missing('parent1', 'parent123', 'parent', 'Robert Mercer', 'robert.mercer@gmail.com', '+91-9876543299',
                           student_id=alex_id)

    conn.close()

    # Auto-rehydrate from attendance_backup.json if present in repository
    backup_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attendance_backup.json")
    if os.path.exists(backup_file):
        try:
            with open(backup_file, "r") as f:
                json_content = f.read()
            if json_content.strip():
                import_data_json(json_content)
                print("[AUTO-RESTORE] Successfully synchronized database from attendance_backup.json.")
        except Exception as e:
            print(f"[AUTO-RESTORE NOTICE]: {e}")

    # Integrity Maintenance: Fix NULL face embeddings and seed sample alerts if fresh install
    try:
        conn = get_db()
        cursor = conn.cursor()

        # Fix any corrupted enrollment states where face_embedding is NULL
        cursor.execute("SELECT id, name, is_face_enrolled, face_embedding, face_photo FROM users WHERE role='student'")
        st_rows = [dict(r) for r in cursor.fetchall()]
        for st in st_rows:
            uid = st["id"]
            if st["is_face_enrolled"] and not st["face_embedding"]:
                vec_file = os.path.join("static", "profiles", f"user_{uid}.npy")
                photo_file = os.path.join("static", "user_photos", f"user_{uid}.jpg")
                if os.path.exists(vec_file):
                    import numpy as np
                    v = np.load(vec_file)
                    v_json = json.dumps(v.tolist())
                    cursor.execute("UPDATE users SET face_embedding=? WHERE id=?", (v_json, uid))
                else:
                    # Reset to pending so admin can clearly enroll them
                    cursor.execute("UPDATE users SET is_face_enrolled=0, face_embedding=NULL WHERE id=?", (uid,))

        # Seed multi-day attendance sessions for October 2026 if sparse records exist (< 10)
        cursor.execute("SELECT COUNT(*) FROM attendance")
        att_c = cursor.fetchone()[0]
        if att_c < 10:
            dates = ['2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09']
            for d in dates:
                code = f"SESS-CS-{d.replace('-', '')}"
                cursor.execute("""INSERT OR IGNORE INTO sessions 
                                  (session_code, teacher_id, teacher_name, subject, department, year, class_name, venue_name, latitude, longitude, radius_meters, scheduled_start, scheduled_end, created_at, expires_at, is_active, geofence_status)
                                  VALUES (?, 3, 'Prof. Sarah Connor', 'CS301: Machine Learning & Cloud Architectures', 'Computer Science & Engineering', '3rd Year', 'CS-A', 'Classroom 101 - CS Block', 17.385044, 78.486671, 25.0, ?, ?, ?, ?, 0, 'APPROVED')""",
                               (code, f"{d} 09:00:00", f"{d} 10:00:00", f"{d} 08:50:00", f"{d} 10:30:00"))
                s_id = cursor.lastrowid or 1

                # Thupakula Karthik (id 32) present all days (100%)
                cursor.execute("""INSERT INTO attendance (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, device_fingerprint, status, is_manual_override, remarks)
                                  VALUES (?, 32, 'Thupakula Karthik', 'CS301: Machine Learning & Cloud Architectures', ?, '09:15:00', 12.5, 0.055, 'DEV_SAFARI_IOS_9114', 'VERIFIED', 0, 'Biometric Verified inside Classroom 101')""",
                               (s_id, d))

                # Alex Mercer (id 30) present Oct 6, 7, 8 (80%)
                if d in ['2026-10-06', '2026-10-07', '2026-10-08']:
                    cursor.execute("""INSERT INTO attendance (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, device_fingerprint, status, is_manual_override, remarks)
                                      VALUES (?, 30, 'Alex Mercer', 'CS301: Machine Learning & Cloud Architectures', ?, '09:12:00', 10.5, 0.045, 'DEV_CHROME_WIN_8271', 'VERIFIED', 0, 'Biometric Verified inside Classroom 101')""",
                                   (s_id, d))

                # Emily Blunt (id 31) present Oct 6 ONLY -> absent Oct 7, 8, 9, 10 (4 CONSECUTIVE DAYS ABSENT)
                if d == '2026-10-06':
                    cursor.execute("""INSERT INTO attendance (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, device_fingerprint, status, is_manual_override, remarks)
                                      VALUES (?, 31, 'Emily Blunt', 'CS301: Machine Learning & Cloud Architectures', ?, '09:14:00', 14.1, 0.052, 'DEV_EMILY_IPHONE', 'VERIFIED', 0, 'Biometric Verified inside Classroom 101')""",
                                   (s_id, d))
                    cursor.execute("""INSERT INTO attendance (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, device_fingerprint, status, is_manual_override, remarks)
                                      VALUES (?, 7, 'Karthik Student', 'CS301: Machine Learning & Cloud Architectures', ?, '09:16:00', 15.0, 0.060, 'DEV_KARTHIK_PHONE', 'VERIFIED', 0, 'Biometric Verified inside Classroom 101')""",
                                   (s_id, d))

        conn.commit()
        conn.close()
        sync_backup_file()
    except Exception as e:
        print(f"[INIT INTEGRITY NOTICE]: {e}")



def sync_backup_file():
    """Silently saves a local JSON snapshot so git commits always retain all registered data."""
    try:
        backup_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attendance_backup.json")
        json_str = export_data_json()
        with open(backup_file, "w") as f:
            f.write(json_str)
    except Exception as e:
        print(f"[SYNC NOTICE]: {e}")

def export_data_json() -> str:
    """Exports all database tables to a JSON payload for cloud backup and multi-device portability."""
    conn = get_db()
    cursor = conn.cursor()
    tables = ["users", "venues", "sessions", "geofence_requests", "attendance", "sms_logs", "departments", "classes", "settings"]
    data = {}
    for t in tables:
        try:
            cursor.execute(f"SELECT * FROM {t}")
            data[t] = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            data[t] = []
    conn.close()
    return json.dumps(data, indent=2)


def import_data_json(json_str: str) -> Tuple[bool, str]:
    """Restores institutional database from a JSON backup payload."""
    try:
        data = json.loads(json_str)
        conn = get_db()
        cursor = conn.cursor()
        for t, rows in data.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join(["?"] * len(cols))
            col_names = ",".join(cols)
            for r in rows:
                vals = [r[c] for c in cols]
                cursor.execute(f"INSERT OR REPLACE INTO {t} ({col_names}) VALUES ({placeholders})", vals)
        conn.commit()
        conn.close()
        return True, "Database restored successfully."
    except Exception as e:
        return False, f"Restore failed: {str(e)}"

if __name__ == '__main__':
    init_db()
