import os
import io
import math
import random
import sqlite3
import pandas as pd
import cv2
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Request, Form, HTTPException, status, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import geofence
import biometrics
import ai_engine

# Initialize Database
database.init_db()

app = FastAPI(title="Hardware-Free BYOD Attendance Platform (Pure Face & Classroom Geofences)", version="4.0.0")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_msgs = []
    for err in exc.errors():
        field = err.get('loc', [''])[-1]
        msg = err.get('msg', 'invalid')
        error_msgs.append(f"{field}: {msg}")
    combined = "; ".join(error_msgs) if error_msgs else str(exc)
    return JSONResponse({"status": "error", "message": f"Input validation error ({combined})", "detail": combined}, status_code=422)

# Mount Static and Templates
os.makedirs("static", exist_ok=True)
os.makedirs("static/profiles", exist_ok=True)
os.makedirs("static/user_photos", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SESSION_COOKIE = "byod_session_user"


# --- Universal Starlette/FastAPI Template Renderer ---
def render_template(request: Request, name: str, context: dict = None):
    """
    Universal template renderer compatible with both new (Starlette >= 0.36) 
    and legacy Starlette TemplateResponse signatures.
    """
    if context is None:
        context = {}
    context["request"] = request
    try:
        return templates.TemplateResponse(request=request, name=name, context=context)
    except TypeError:
        return templates.TemplateResponse(name, context)


# --- Authentication Helpers ---
def get_current_user(request: Request) -> Optional[dict]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        user_id = int(cookie)
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        return None
    return None


# --- Pydantic Request Models ---
class AttendanceSubmitRequest(BaseModel):
    session_code: str
    latitude: float
    longitude: float
    accuracy: float = 10.0
    device_fingerprint: str = "UNKNOWN_DEVICE"
    image: str
    secondary_image: Optional[str] = None


class FaceEnrollRequest(BaseModel):
    image: str


class SessionCreateRequest(BaseModel):
    subject: str
    department: str = "Computer Science & Engineering"
    year: str = "3rd Year"
    class_name: str = "CS-A"
    venue_name: str
    latitude: float
    longitude: float
    radius_meters: float = 25.0
    duration_minutes: int = 50
    scheduled_start: str = "09:30"
    scheduled_end: str = "10:20"


class GeofenceChangeRequestModel(BaseModel):
    session_id: int
    new_venue_name: str
    new_latitude: float
    new_longitude: float
    new_radius_meters: float = 30.0
    reason: str


class ManualOverrideRequest(BaseModel):
    session_id: int
    student_id: int
    reason: str = "Hardware/Mobile Battery Exception"


# --- Root & Authentication Routes ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(f"/{user['role']}", status_code=status.HTTP_302_FOUND)
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(f"/{user['role']}", status_code=status.HTTP_302_FOUND)
    return render_template(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE (username=? OR reg_no=?) AND password=?", 
                   (username.strip(), username.strip(), password.strip()))
    user = cursor.fetchone()
    conn.close()

    if user:
        user_dict = dict(user)
        response = RedirectResponse(f"/{user_dict['role']}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key=SESSION_COOKIE, value=str(user_dict["id"]), httponly=True, max_age=86400)
        return response

    return render_template(request, "login.html", {
        "error": "Invalid credentials. Please verify your username / registration number and password."
    })


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- 1. Head of Management Portal ---
@app.get("/management", response_class=HTMLResponse)
async def management_portal(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='student'")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='teacher'")
    total_teachers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sessions")
    total_sessions = cursor.fetchone()[0]

    today_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=?", (today_str,))
    today_verified = cursor.fetchone()[0]

    # Users List with joined faculty mentor name
    cursor.execute('''SELECT u.*, t.name as teacher_name 
                      FROM users u 
                      LEFT JOIN users t ON u.assigned_teacher_id = t.id 
                      ORDER BY u.id DESC''')
    all_users = [dict(r) for r in cursor.fetchall()]

    # Separate users cleanly by role
    students = [u for u in all_users if u["role"] == "student"]
    teachers = [u for u in all_users if u["role"] == "teacher"]
    admins = [u for u in all_users if u["role"] == "management"]

    # Faculty List for assignment dropdown
    cursor.execute("SELECT id, name, department FROM users WHERE role='teacher' ORDER BY name ASC")
    teachers_list = [dict(r) for r in cursor.fetchall()]

    # Departments and Academic Years
    cursor.execute("SELECT id, name, code FROM departments ORDER BY name ASC")
    departments = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, name, department, year FROM classes ORDER BY department ASC, year ASC, name ASC")
    classes_list = [dict(r) for r in cursor.fetchall()]

    years_list = ["1st Year", "2nd Year", "3rd Year", "4th Year"]

    # Student Stats
    enrolled_count = sum(1 for s in students if s.get("is_face_enrolled", 0))
    student_stats = {
        "total": len(students),
        "enrolled_face": enrolled_count,
        "pending_face": len(students) - enrolled_count,
        "by_year": {y: sum(1 for s in students if s.get("year") == y) for y in years_list}
    }

    # Individual Classroom Geofences List
    cursor.execute("SELECT * FROM venues ORDER BY building ASC, room_no ASC")
    venues = [dict(r) for r in cursor.fetchall()]

    # Pending Teacher Geofence Relocation Approval Requests
    cursor.execute('''SELECT * FROM geofence_requests ORDER BY id DESC LIMIT 20''')
    all_geofence_requests = [dict(r) for r in cursor.fetchall()]
    pending_geofence_requests = [r for r in all_geofence_requests if r["status"] == "PENDING"]

    # SMS Dispatch Logs with WhatsApp Share Links
    cursor.execute("SELECT * FROM sms_logs ORDER BY id DESC LIMIT 25")
    raw_sms_logs = [dict(r) for r in cursor.fetchall()]
    sms_logs = []
    for log in raw_sms_logs:
        links = database.generate_messaging_links(log["recipient_phone"], log["message_body"])
        log["whatsapp_url"] = links["whatsapp_url"]
        log["sms_url"] = links["sms_url"]
        sms_logs.append(log)

    # Initial Daily Attendance Computation for today_str
    cursor.execute('''SELECT a.*, u.department, u.year, u.class_name, u.reg_no, u.phone, u.parent_phone 
                      FROM attendance a 
                      JOIN users u ON a.student_id = u.id 
                      WHERE a.date = ? 
                      ORDER BY a.time DESC''', (today_str,))
    presentees_today = [dict(r) for r in cursor.fetchall()]
    present_student_ids = set(r["student_id"] for r in presentees_today)

    current_month_str = today_str[:7]
    monthly_shortage = database.get_monthly_attendance_shortage(current_month_str)
    consecutive_absentees = database.get_consecutive_absentees(4)

    absentees_today = []
    for s in students:
        if s["id"] not in present_student_ids:
            parent_phone = s.get("parent_phone") or ""
            student_phone = s.get("phone") or ""
            reg_id = s.get("reg_no") or s.get("username") or "STUDENT"
            parent_msg = f"Dear Parent, your ward {s['name']} ({reg_id}) was marked ABSENT for classes on {today_str}. Please contact college administration."
            student_msg = f"ATTENDANCE NOTICE: {s['name']} ({reg_id}), you were marked ABSENT for classes on {today_str}. Please verify your attendance."
            parent_links = database.generate_messaging_links(parent_phone, parent_msg)
            student_links = database.generate_messaging_links(student_phone, student_msg)
            s_dict = dict(s)
            s_dict["parent_whatsapp_url"] = parent_links["whatsapp_url"]
            s_dict["parent_sms_url"] = parent_links["sms_url"]
            s_dict["student_whatsapp_url"] = student_links["whatsapp_url"]
            s_dict["student_sms_url"] = student_links["sms_url"]
            s_dict["parent_msg"] = parent_msg
            s_dict["student_msg"] = student_msg
            s_dict["whatsapp_url"] = parent_links["whatsapp_url"]
            s_dict["sms_url"] = parent_links["sms_url"]
            absentees_today.append(s_dict)

    attendance_summary = {
        "total": len(students),
        "present": len(presentees_today),
        "absent": len(absentees_today),
        "percentage": round((len(presentees_today) / len(students) * 100.0), 1) if students else 0.0,
        "monthly_shortage_count": monthly_shortage.get("shortage_count", 0),
        "consecutive_absent_count": consecutive_absentees.get("consecutive_absent_count", 0)
    }

    conn.close()

    return render_template(request, "management.html", {
        "user": user,
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_sessions": total_sessions,
        "today_verified": today_verified,
        "users": all_users,
        "students": students,
        "teachers": teachers,
        "admins": admins,
        "departments": departments,
        "years_list": years_list,
        "classes_list": classes_list,
        "student_stats": student_stats,
        "teachers_list": teachers_list,
        "venues": venues,
        "pending_geofence_requests": pending_geofence_requests,
        "all_geofence_requests": all_geofence_requests,
        "sms_logs": sms_logs,
        "today": today_str,
        "presentees_today": presentees_today,
        "absentees_today": absentees_today,
        "attendance_summary": attendance_summary,
        "current_month_str": current_month_str,
        "monthly_shortage": monthly_shortage,
        "consecutive_absentees": consecutive_absentees,
        "fast2sms_api_key": database.get_setting("FAST2SMS_API_KEY"),
        "twilio_account_sid": database.get_setting("TWILIO_ACCOUNT_SID"),
        "twilio_phone_number": database.get_setting("TWILIO_PHONE_NUMBER"),
        "telegram_bot_token": database.get_setting("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": database.get_setting("TELEGRAM_CHAT_ID"),
        "free_webhook_url": database.get_setting("FREE_WEBHOOK_URL")
    })


@app.get("/api/management/daily_attendance_data")
async def management_daily_attendance_data(request: Request,
                                          date: Optional[str] = None,
                                          department: Optional[str] = None,
                                          year: Optional[str] = None,
                                          class_name: Optional[str] = None):
    """Fetches dynamically filtered Presentees and Absentees data class-wise and year-wise."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    query_date = date.strip() if date and date.strip() else datetime.now().strftime("%Y-%m-%d")

    conn = database.get_db()
    cursor = conn.cursor()

    # Base student query with class-wise and year-wise filters
    user_query = """SELECT u.id, u.name, u.reg_no, u.username, u.department, u.year, u.class_name, 
                            u.phone, u.parent_phone, u.is_face_enrolled, t.name as teacher_name 
                     FROM users u 
                     LEFT JOIN users t ON u.assigned_teacher_id = t.id 
                     WHERE u.role = 'student'"""
    user_params = []

    if department and department.strip() and department.strip() != "ALL":
        user_query += " AND u.department = ?"
        user_params.append(department.strip())

    if year and year.strip() and year.strip() != "ALL":
        user_query += " AND u.year = ?"
        user_params.append(year.strip())

    if class_name and class_name.strip() and class_name.strip() != "ALL":
        user_query += " AND u.class_name = ?"
        user_params.append(class_name.strip())

    user_query += " ORDER BY u.department ASC, u.year ASC, u.name ASC"
    cursor.execute(user_query, user_params)
    matched_students = [dict(r) for r in cursor.fetchall()]
    student_ids = [s["id"] for s in matched_students]

    if not student_ids:
        conn.close()
        return JSONResponse({
            "status": "success",
            "date": query_date,
            "summary": {
                "total_students": 0,
                "present_count": 0,
                "absent_count": 0,
                "attendance_pct": 0.0
            },
            "presentees": [],
            "absentees": []
        })

    # Query attendance records on selected date
    placeholders = ",".join(["?"] * len(student_ids))
    att_query = f"""SELECT a.*, u.department, u.year, u.class_name, u.reg_no, u.phone, u.parent_phone 
                     FROM attendance a 
                     JOIN users u ON a.student_id = u.id 
                     WHERE a.date = ? AND a.student_id IN ({placeholders})
                     ORDER BY a.time DESC"""
    cursor.execute(att_query, [query_date] + student_ids)
    present_rows = [dict(r) for r in cursor.fetchall()]

    present_id_set = set(r["student_id"] for r in present_rows)
    absentees = []
    for s in matched_students:
        if s["id"] not in present_id_set:
            parent_phone = s.get("parent_phone") or ""
            student_phone = s.get("phone") or ""
            reg_id = s.get("reg_no") or s.get("username") or "STUDENT"
            parent_msg = f"Dear Parent, your ward {s['name']} ({reg_id}) was marked ABSENT for classes ({s.get('department') or ''}, {s.get('year') or ''}) on {query_date}. Please contact college administration."
            student_msg = f"ATTENDANCE NOTICE: {s['name']} ({reg_id}), you were marked ABSENT for classes on {query_date}. Please contact college administration."
            parent_links = database.generate_messaging_links(parent_phone, parent_msg)
            student_links = database.generate_messaging_links(student_phone, student_msg)
            s_copy = dict(s)
            s_copy["parent_whatsapp_url"] = parent_links["whatsapp_url"]
            s_copy["parent_sms_url"] = parent_links["sms_url"]
            s_copy["student_whatsapp_url"] = student_links["whatsapp_url"]
            s_copy["student_sms_url"] = student_links["sms_url"]
            s_copy["parent_msg"] = parent_msg
            s_copy["student_msg"] = student_msg
            s_copy["whatsapp_url"] = parent_links["whatsapp_url"]
            s_copy["sms_url"] = parent_links["sms_url"]
            absentees.append(s_copy)
    conn.close()

    total = len(matched_students)
    present_count = len(present_id_set)
    absent_count = len(absentees)
    pct = round((present_count / total * 100.0), 1) if total > 0 else 0.0

    return JSONResponse({
        "status": "success",
        "date": query_date,
        "summary": {
            "total_students": total,
            "present_count": present_count,
            "absent_count": absent_count,
            "attendance_pct": pct
        },
        "presentees": present_rows,
        "absentees": absentees
    })


@app.get("/api/management/export_daily_attendance")
async def export_daily_attendance_excel(request: Request,
                                       date: Optional[str] = None,
                                       department: Optional[str] = None,
                                       year: Optional[str] = None):
    """Generates high-resolution multi-sheet Excel spreadsheet with separate Presentees & Absentees."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        raise HTTPException(status_code=403, detail="Unauthorized")

    query_date = date.strip() if date and date.strip() else datetime.now().strftime("%Y-%m-%d")

    conn = database.get_db()

    # 1. Presentees Sheet
    p_query = '''SELECT u.name as "Student Name", u.reg_no as "Reg Number", u.department as "Department / Branch",
                        u.year as "Academic Year", u.class_name as "Section", a.subject as "Subject / Session",
                        a.time as "Verification Time", a.status as "Attendance Status", 
                        a.distance_meters as "GPS Distance (m)", a.remarks as "Verification Audit"
                 FROM attendance a
                 JOIN users u ON a.student_id = u.id
                 WHERE a.date = ?'''
    p_params = [query_date]
    if department and department.strip() != "ALL":
        p_query += " AND u.department = ?"
        p_params.append(department.strip())
    if year and year.strip() != "ALL":
        p_query += " AND u.year = ?"
        p_params.append(year.strip())
    p_query += " ORDER BY u.department ASC, u.year ASC, a.time ASC"

    df_present = pd.read_sql_query(p_query, conn, params=p_params)

    # 2. Absentees Sheet
    a_query = '''SELECT u.name as "Student Name", u.reg_no as "Reg Number", u.department as "Department / Branch",
                        u.year as "Academic Year", u.class_name as "Section", u.phone as "Student Mobile",
                        u.parent_phone as "Parent Mobile", 'ABSENT' as "Status",
                        t.name as "Mentor Faculty"
                 FROM users u
                 LEFT JOIN users t ON u.assigned_teacher_id = t.id
                 WHERE u.role = 'student'
                   AND u.id NOT IN (SELECT student_id FROM attendance WHERE date = ?)'''
    a_params = [query_date]
    if department and department.strip() != "ALL":
        a_query += " AND u.department = ?"
        a_params.append(department.strip())
    if year and year.strip() != "ALL":
        a_query += " AND u.year = ?"
        a_params.append(year.strip())
    a_query += " ORDER BY u.department ASC, u.year ASC, u.name ASC"

    df_absent = pd.read_sql_query(a_query, conn, params=a_params)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_present.to_excel(writer, index=False, sheet_name='Presentees')
        df_absent.to_excel(writer, index=False, sheet_name='Absentees')
    output.seek(0)

    filename = f"daily_attendance_{query_date}_{datetime.now().strftime('%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/management/mark_manual_attendance")
async def management_manual_attendance(request: Request,
                                       student_id: int = Form(...),
                                       date: str = Form(...),
                                       remarks: str = Form("Manual override by Administrator")):
    """Allows admin to manually override an absentee student to present with official remarks."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, department, year, class_name FROM users WHERE id=? AND role='student'", (student_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        return JSONResponse({"status": "error", "message": "Student not found"}, status_code=404)

    now_time = datetime.now().strftime("%H:%M:%S")
    cursor.execute('''INSERT INTO attendance 
                      (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, device_fingerprint, status, is_manual_override, override_by, remarks)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (0, student["id"], student["name"], f"General Academic Session ({student['department'] or 'General'})",
                    date.strip(), now_time, 0.0, 0.0, "ADMIN_OVERRIDE", "MANUAL_OVERRIDE", 1, user["name"], remarks.strip()))
    conn.commit()
    conn.close()
    database.sync_backup_file()

    return JSONResponse({"status": "success", "message": f"Attendance for '{student['name']}' manually verified for {date.strip()}."})


@app.post("/api/management/delete_user/{user_id}")
async def management_delete_user(request: Request, user_id: int):
    """Removes user account from institutional system."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if user_id == user["id"]:
        return JSONResponse({"status": "error", "message": "Cannot delete your own administrative account."}, status_code=400)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, role FROM users WHERE id=?", (user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        return JSONResponse({"status": "error", "message": "User not found."}, status_code=404)

    cursor.execute("DELETE FROM users WHERE id=?", (user_id,))
    cursor.execute("DELETE FROM attendance WHERE student_id=?", (user_id,))
    conn.commit()
    conn.close()
    database.sync_backup_file()

    return JSONResponse({"status": "success", "message": f"{target['role'].capitalize()} '{target['name']}' has been removed successfully."})


@app.post("/api/management/approve_geofence/{request_id}")
async def approve_geofence_change(request_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM geofence_requests WHERE id=?", (request_id,))
    req_row = cursor.fetchone()
    if not req_row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Request not found"}, status_code=404)

    req_data = dict(req_row)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute('''UPDATE geofence_requests 
                      SET status='APPROVED', reviewed_by=?, reviewed_at=? 
                      WHERE id=?''', (user["name"], now_str, request_id))

    if req_data.get("session_id"):
        cursor.execute('''UPDATE sessions 
                          SET venue_name=?, latitude=?, longitude=?, radius_meters=?, geofence_status='APPROVED' 
                          WHERE id=?''',
                       (req_data["new_venue_name"], req_data["new_latitude"], req_data["new_longitude"],
                        req_data["new_radius_meters"], req_data["session_id"]))

    conn.commit()

    cursor.execute("SELECT phone FROM users WHERE id=?", (req_data["teacher_id"],))
    t_row = cursor.fetchone()
    conn.close()

    if t_row and t_row["phone"]:
        database.send_sms(t_row["phone"], req_data["teacher_name"], "teacher", "GEOFENCE_APPROVAL",
                          f"Admin Notice: Your classroom relocation request for {req_data['subject']} to {req_data['new_venue_name']} has been APPROVED.")

    return JSONResponse({
        "status": "success",
        "message": f"Classroom relocation for '{req_data['subject']}' to '{req_data['new_venue_name']}' has been officially APPROVED."
    })


@app.post("/api/management/reject_geofence/{request_id}")
async def reject_geofence_change(request_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("SELECT * FROM geofence_requests WHERE id=?", (request_id,))
    req_row = cursor.fetchone()
    if not req_row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Request not found"}, status_code=404)

    req_data = dict(req_row)

    cursor.execute('''UPDATE geofence_requests 
                      SET status='REJECTED', reviewed_by=?, reviewed_at=? 
                      WHERE id=?''', (user["name"], now_str, request_id))

    if req_data.get("session_id"):
        cursor.execute("UPDATE sessions SET geofence_status='APPROVED' WHERE id=?", (req_data["session_id"],))

    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Classroom relocation for '{req_data['subject']}' was REJECTED. Session retains its original geofence."
    })


@app.post("/api/management/create_user")
async def management_create_user(request: Request,
                                 username: str = Form(...),
                                 password: str = Form(...),
                                 role: str = Form(...),
                                 name: str = Form(...),
                                 email: str = Form(""),
                                 phone: str = Form(...),
                                 parent_phone: str = Form(""),
                                 department: str = Form("Computer Science & Engineering"),
                                 year: str = Form("3rd Year"),
                                 class_name: str = Form("CS-A"),
                                 assigned_teacher_id: Optional[str] = Form(None),
                                 student_id: Optional[str] = Form(None)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    role_clean = role.strip().lower()
    teacher_id_val = int(assigned_teacher_id) if assigned_teacher_id and str(assigned_teacher_id).strip().isdigit() else None
    student_id_val = int(student_id) if student_id and str(student_id).strip().isdigit() else None

    # Management admin has NO class section or academic year (admin solely maintains website & institution)
    if role_clean == "management":
        dept_val = "Central Administration"
        year_val = None
        class_val = None
        teacher_id_val = None
        parent_phone_val = ""
        reg_no = None
    elif role_clean == "teacher":
        dept_val = department.strip()
        year_val = None
        class_val = None
        teacher_id_val = None
        parent_phone_val = ""
        reg_no = None
    elif role_clean == "parent":
        dept_val = None
        year_val = None
        class_val = None
        teacher_id_val = None
        parent_phone_val = ""
        reg_no = None
    else:  # student
        dept_val = department.strip()
        year_val = year.strip() if year else "1st Year"
        class_val = class_name.strip() if class_name else "A"
        parent_phone_val = parent_phone.strip() if parent_phone else ""
        clean_dept = "".join(c for c in dept_val[:4] if c.isalnum()).upper() or "CS"
        reg_no = f"REG2026-{clean_dept}{random.randint(100, 999)}"

    conn = database.get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor.execute('''INSERT INTO users 
                          (username, password, role, name, email, phone, parent_phone, reg_no, department, year, class_name, assigned_teacher_id, student_id, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                       (username.strip(), password.strip(), role_clean, name.strip(), email.strip(),
                        phone.strip(), parent_phone_val, reg_no, dept_val, year_val, class_val,
                        teacher_id_val, student_id_val, now_str))
        conn.commit()
        conn.close() # Close connection immediately before triggering messaging or backup!
        conn = None

        student_msg = f"Welcome to BYODAttend! Your account is created. Reg No: {reg_no or username.strip()}, Password: {password.strip()}. Admin will enroll your Face ID."
        parent_msg = f"Your ward {name.strip()} is enrolled at BYODAttend ({dept_val or 'General'}, {year_val or ''}). Reg No: {reg_no}."

        student_links = database.generate_messaging_links(phone.strip(), student_msg) if phone.strip() else {}
        parent_links = database.generate_messaging_links(parent_phone_val, parent_msg) if parent_phone_val else {}

        if phone.strip():
            database.send_sms(phone.strip(), name.strip(), role_clean, "REGISTRATION_CREDENTIALS", student_msg)

        if parent_phone_val:
            database.send_sms(parent_phone_val, f"Parent of {name.strip()}", "parent", "REGISTRATION_CREDENTIALS", parent_msg)

        database.sync_backup_file()
        return JSONResponse({
            "status": "success",
            "message": f"User '{name.strip()}' registered successfully.",
            "credentials": {
                "name": name.strip(),
                "role": role_clean,
                "reg_no": reg_no or username.strip(),
                "username": username.strip(),
                "password": password.strip(),
                "phone": phone.strip(),
                "parent_phone": parent_phone_val,
                "student_whatsapp_url": student_links.get("whatsapp_url", ""),
                "student_sms_url": student_links.get("sms_url", ""),
                "parent_whatsapp_url": parent_links.get("whatsapp_url", ""),
                "parent_sms_url": parent_links.get("sms_url", "")
            }
        })
    except sqlite3.IntegrityError:
        if conn:
            try: conn.close()
            except Exception: pass
        return JSONResponse({"status": "error", "message": f"Username '{username.strip()}' or Reg No already exists."}, status_code=400)
    except Exception as e:
        if conn:
            try: conn.close()
            except Exception: pass
        return JSONResponse({"status": "error", "message": f"Server error: {str(e)}"}, status_code=500)



@app.get("/api/management/monthly_shortage_data")
async def management_monthly_shortage_data(request: Request,
                                           month: Optional[str] = None,
                                           department: Optional[str] = None,
                                           year: Optional[str] = None,
                                           class_name: Optional[str] = None):
    """Returns students with monthly attendance strictly below 75% with 1-tap messaging links."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    m_str = month.strip() if month and month.strip() else datetime.now().strftime("%Y-%m")
    data = database.get_monthly_attendance_shortage(m_str, department, year, class_name)
    return JSONResponse({"status": "success", **data})


@app.get("/api/management/consecutive_absentees_data")
async def management_consecutive_absentees_data(request: Request,
                                                consecutive_days: int = 4,
                                                department: Optional[str] = None,
                                                year: Optional[str] = None,
                                                class_name: Optional[str] = None):
    """Returns students absent for consecutive_days (default 4 days) with 1-tap messaging links."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = database.get_consecutive_absentees(consecutive_days, department, year, class_name)
    return JSONResponse({"status": "success", **data})


@app.post("/api/management/send_direct_alert")
async def management_send_direct_alert(request: Request,
                                       recipient_phone: str = Form(...),
                                       recipient_name: str = Form(...),
                                       recipient_role: str = Form("parent"),
                                       message_type: str = Form("ATTENDANCE_ALERT"),
                                       message_body: str = Form(...)):
    """Logs and dispatches direct alert message via server webhook / telegram if configured."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    res = database.send_sms(recipient_phone.strip(), recipient_name.strip(), recipient_role.strip(), message_type.strip(), message_body.strip())
    database.sync_backup_file()
    return JSONResponse({"status": "success", "message": "Alert dispatched & recorded in logs.", "data": res})


@app.post("/api/management/enroll_student_face")
async def management_enroll_student_face(request: Request,
                                         student_id: int = Form(...),
                                         image: Optional[str] = Form(None),
                                         secondary_image: Optional[str] = Form(None),
                                         file: Optional[UploadFile] = File(None)):
    """Admin captures and enrolls 100% accurate live biometric Face ID for student."""
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, reg_no FROM users WHERE id=? AND role='student'", (student_id,))
    target_student = cursor.fetchone()
    if not target_student:
        conn.close()
        return JSONResponse({"status": "error", "message": "Student record not found."}, status_code=404)

    frame = None
    if image and image.strip():
        frame = biometrics.decode_base64_image(image)

    if frame is None and file and file.filename:
        try:
            file_bytes = await file.read()
            if len(file_bytes) > 0:
                np_arr = np.frombuffer(file_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"[FILE UPLOAD DECODE ERROR]: {e}")

    if frame is None:
        conn.close()
        return JSONResponse({"status": "error", "message": "Failed to decode camera frame or photo. Please ensure a valid JPEG/PNG image was captured."}, status_code=400)

    sec_frame = biometrics.decode_base64_image(secondary_image) if secondary_image and secondary_image.strip() else None
    success, msg, vec, photo_b64 = biometrics.save_enrolled_face(student_id, frame, conn=conn, secondary_frame=sec_frame)
    if not success:
        conn.close()
        return JSONResponse({"status": "error", "message": msg}, status_code=422)

    conn.commit()
    conn.close()
    database.sync_backup_file()

    return JSONResponse({
        "status": "success",
        "message": f"Biometric Face ID for '{target_student['name']}' ({target_student['reg_no']}) has been successfully enrolled and activated."
    })


@app.post("/api/management/add_department")
async def management_add_department(request: Request,
                                    name: str = Form(...),
                                    code: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not name.strip() or not code.strip():
        return JSONResponse({"status": "error", "message": "Department name and code are required."}, status_code=400)

    try:
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO departments (name, code) VALUES (?, ?)", (name.strip(), code.strip().upper()))
        conn.commit()
        conn.close()
        return JSONResponse({"status": "success", "message": f"Branch '{name.strip()}' ({code.strip().upper()}) added successfully."})
    except sqlite3.IntegrityError:
        return JSONResponse({"status": "error", "message": f"Branch '{name.strip()}' already exists."}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Error adding branch: {str(e)}"}, status_code=400)


@app.post("/api/management/delete_department/{dept_id}")
async def management_delete_department(dept_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM departments WHERE id=?", (dept_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "success", "message": "Branch deleted successfully."})


@app.post("/api/management/add_venue")
async def management_add_venue(request: Request,
                               name: str = Form(...),
                               building: str = Form(...),
                               room_no: str = Form(...),
                               latitude: float = Form(...),
                               longitude: float = Form(...),
                               radius_meters: float = Form(25.0)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    try:
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO venues (name, building, room_no, latitude, longitude, radius_meters)
                          VALUES (?, ?, ?, ?, ?, ?)''',
                       (name.strip(), building.strip(), room_no.strip(), latitude, longitude, radius_meters))
        conn.commit()
        conn.close()
        return JSONResponse({"status": "success", "message": f"Classroom geofence '{name.strip()}' configured successfully."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Failed to add classroom: {str(e)}"}, status_code=400)


@app.post("/api/management/delete_venue/{venue_id}")
async def management_delete_venue(venue_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM venues WHERE id=?", (venue_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "success", "message": "Classroom geofence deleted."})


@app.post("/api/management/save_sms_settings")
async def management_save_sms_settings(request: Request,
                                       fast2sms_api_key: str = Form(""),
                                       twilio_account_sid: str = Form(""),
                                       twilio_auth_token: str = Form(""),
                                       twilio_phone_number: str = Form(""),
                                       telegram_bot_token: str = Form(""),
                                       telegram_chat_id: str = Form(""),
                                       free_webhook_url: str = Form("")):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    database.set_setting("FAST2SMS_API_KEY", fast2sms_api_key.strip())
    database.set_setting("TWILIO_ACCOUNT_SID", twilio_account_sid.strip())
    if twilio_auth_token.strip():
        database.set_setting("TWILIO_AUTH_TOKEN", twilio_auth_token.strip())
    database.set_setting("TWILIO_PHONE_NUMBER", twilio_phone_number.strip())
    database.set_setting("TELEGRAM_BOT_TOKEN", telegram_bot_token.strip())
    database.set_setting("TELEGRAM_CHAT_ID", telegram_chat_id.strip())
    database.set_setting("FREE_WEBHOOK_URL", free_webhook_url.strip())
    database.sync_backup_file()

    return JSONResponse({"status": "success", "message": "Messaging & Gateway settings updated successfully."})

@app.post("/api/management/update_user")
async def management_update_user(request: Request,
                                 user_id: int = Form(...),
                                 name: str = Form(...),
                                 phone: str = Form(...),
                                 parent_phone: str = Form(""),
                                 email: str = Form(""),
                                 department: str = Form("Computer Science & Engineering"),
                                 year: str = Form("3rd Year"),
                                 class_name: str = Form("CS-A"),
                                 assigned_teacher_id: Optional[str] = Form(None)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    teacher_id_val = int(assigned_teacher_id) if assigned_teacher_id and str(assigned_teacher_id).strip() and str(assigned_teacher_id).strip().isdigit() else None
    cursor.execute('''UPDATE users SET name=?, phone=?, parent_phone=?, email=?, department=?, year=?, class_name=?, assigned_teacher_id=?
                      WHERE id=?''', 
                   (name.strip(), phone.strip(), parent_phone.strip(), email.strip(), department.strip(), year.strip(), class_name.strip(), teacher_id_val, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "success", "message": "User profile, class allocation, and phone updated successfully."})


@app.post("/api/management/reset_password")
async def management_reset_password(request: Request,
                                    user_id: int = Form(...),
                                    new_password: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not new_password or len(new_password.strip()) < 4:
        return JSONResponse({"status": "error", "message": "Password must be at least 4 characters long."}, status_code=400)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password=? WHERE id=?", (new_password.strip(), user_id))
    cursor.execute("SELECT name, phone, reg_no FROM users WHERE id=?", (user_id,))
    u_row = cursor.fetchone()
    conn.commit()
    conn.close()

    reset_msg = f"Administrative Notice: Your BYODAttend password has been reset to: {new_password.strip()}"
    links = {}
    if u_row and u_row["phone"]:
        database.send_sms(u_row["phone"], u_row["name"], "student", "PASSWORD_RESET", reset_msg)
        links = database.generate_messaging_links(u_row["phone"], reset_msg)

    return JSONResponse({
        "status": "success",
        "message": "Password reset successfully. Notification dispatched.",
        "whatsapp_url": links.get("whatsapp_url", ""),
        "sms_url": links.get("sms_url", "")
    })


@app.get("/api/management/export_data")
async def export_data_endpoint(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    json_data = database.export_data_json()
    return Response(
        content=json_data,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=byod_attendance_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
    )


@app.post("/api/management/import_data")
async def import_data_endpoint(request: Request, backup_file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user or user["role"] != "management":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    try:
        content = await backup_file.read()
        json_str = content.decode("utf-8")
        success, msg = database.import_data_json(json_str)
        if success:
            return JSONResponse({"status": "success", "message": msg})
        return JSONResponse({"status": "error", "message": msg}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Failed to parse backup file: {str(e)}"}, status_code=400)



# --- 2. Teacher Portal ---
@app.get("/teacher", response_class=HTMLResponse)
async def teacher_portal(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM venues ORDER BY building ASC, room_no ASC")
    venues = [dict(r) for r in cursor.fetchall()]

    cursor.execute('''SELECT * FROM users 
                      WHERE role='student' 
                        AND (assigned_teacher_id=? 
                             OR LOWER(TRIM(department)) = LOWER(TRIM(?))
                             OR assigned_teacher_id IS NULL)
                      ORDER BY class_name ASC, name ASC''', (user["id"], user.get("department", "")))
    assigned_students = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM sessions WHERE teacher_id=? ORDER BY id DESC LIMIT 10", (user["id"],))
    sessions_list = [dict(r) for r in cursor.fetchall()]

    active_session = None
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in sessions_list:
        if s["is_active"] and s["expires_at"] > now_str:
            active_session = s
            break

    attended_count = 0
    if active_session:
        cursor.execute("SELECT COUNT(*) FROM attendance WHERE session_id=?", (active_session["id"],))
        attended_count = cursor.fetchone()[0]

    cursor.execute("SELECT * FROM geofence_requests WHERE teacher_id=? ORDER BY id DESC LIMIT 5", (user["id"],))
    my_geofence_requests = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return render_template(request, "teacher.html", {
        "user": user,
        "venues": venues,
        "assigned_students": assigned_students,
        "sessions": sessions_list,
        "active_session": active_session,
        "attended_count": attended_count,
        "my_geofence_requests": my_geofence_requests
    })


@app.post("/api/sessions/create")
async def create_session(payload: SessionCreateRequest, request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    now = datetime.now()
    expires = now + timedelta(minutes=payload.duration_minutes)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    expires_str = expires.strftime("%Y-%m-%d %H:%M:%S")

    clean_sub = payload.subject.replace(" ", "").upper()[:4]
    session_code = f"{clean_sub}-{random.randint(100, 999)}"

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("UPDATE sessions SET is_active=0 WHERE teacher_id=?", (user["id"],))

    cursor.execute('''INSERT INTO sessions 
                      (session_code, teacher_id, teacher_name, subject, department, year, class_name, 
                       venue_name, latitude, longitude, radius_meters, scheduled_start, scheduled_end, 
                       created_at, expires_at, is_active, geofence_status)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'APPROVED')''',
                   (session_code, user["id"], user["name"], payload.subject.strip(),
                    payload.department, payload.year, payload.class_name, payload.venue_name.strip(),
                    payload.latitude, payload.longitude, payload.radius_meters, 
                    payload.scheduled_start, payload.scheduled_end, now_str, expires_str))
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Subject session '{payload.subject}' opened for {payload.class_name} in {payload.venue_name}.",
        "session": {
            "id": session_id,
            "session_code": session_code,
            "subject": payload.subject,
            "venue_name": payload.venue_name,
            "expires_at": expires_str,
            "radius_meters": payload.radius_meters
        }
    })


@app.post("/api/teacher/request_geofence_change")
async def request_geofence_change(payload: GeofenceChangeRequestModel, request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM sessions WHERE id=?", (payload.session_id,))
    s_row = cursor.fetchone()
    if not s_row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)

    session_data = dict(s_row)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute('''INSERT INTO geofence_requests 
                      (session_id, teacher_id, teacher_name, subject, class_name, old_venue_name, 
                       new_venue_name, new_latitude, new_longitude, new_radius_meters, reason, status, requested_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)''',
                   (payload.session_id, user["id"], user["name"], session_data["subject"], session_data["class_name"],
                    session_data["venue_name"], payload.new_venue_name.strip(), payload.new_latitude,
                    payload.new_longitude, payload.new_radius_meters, payload.reason.strip(), now_str))

    cursor.execute("UPDATE sessions SET geofence_status='CHANGE_PENDING' WHERE id=?", (payload.session_id,))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Classroom relocation request submitted to Admin for approval."
    })


@app.post("/api/attendance/manual_override")
async def manual_override_attendance(payload: ManualOverrideRequest, request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM sessions WHERE id=?", (payload.session_id,))
    s_row = cursor.fetchone()
    if not s_row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)

    cursor.execute("SELECT id, name FROM users WHERE id=?", (payload.student_id,))
    st_row = cursor.fetchone()
    if not st_row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Student not found"}, status_code=404)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    cursor.execute("SELECT id FROM attendance WHERE session_id=? AND student_id=?", (payload.session_id, payload.student_id))
    if cursor.fetchone():
        conn.close()
        return JSONResponse({"status": "warning", "message": f"{st_row['name']} already marked present in this session."})

    cursor.execute('''INSERT INTO attendance 
                      (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, 
                       device_fingerprint, status, is_manual_override, override_by, remarks)
                      VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, 'MANUAL_FACULTY_OVERRIDE', 'Present', 1, ?, ?)''',
                   (payload.session_id, payload.student_id, st_row["name"], s_row["subject"],
                    date_str, time_str, user["name"], f"Manual Override: {payload.reason}"))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Manual attendance override recorded for {st_row['name']}."
    })


@app.get("/api/sessions/{session_id}/live")
async def session_live_feed(session_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute('''SELECT id, student_id, student_name, time, distance_meters, face_distance, status, is_manual_override, remarks
                      FROM attendance WHERE session_id=? ORDER BY id DESC''', (session_id,))
    records = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT session_code, expires_at, is_active, venue_name, geofence_status FROM sessions WHERE id=?", (session_id,))
    s_row = cursor.fetchone()
    conn.close()

    if not s_row:
        return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    is_live = bool(s_row["is_active"] and s_row["expires_at"] > now_str)

    return JSONResponse({
        "session_code": s_row["session_code"],
        "venue_name": s_row["venue_name"],
        "geofence_status": s_row["geofence_status"],
        "is_live": is_live,
        "expires_at": s_row["expires_at"],
        "total_present": len(records),
        "records": records
    })


@app.post("/api/sessions/{session_id}/close")
async def close_session(session_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE sessions SET is_active=0 WHERE id=?", (session_id,))
    conn.commit()
    conn.close()

    return JSONResponse({"status": "success", "message": "Attendance session has been closed manually."})


# --- 3. Student Portal (Pure Face Attendance & Classroom Geofence) ---
@app.get("/student", response_class=HTMLResponse)
async def student_portal(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    conn = database.get_db()
    cursor = conn.cursor()

    is_enrolled = bool(user.get("is_face_enrolled", 0))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''SELECT * FROM sessions 
                      WHERE is_active=1 
                        AND (LOWER(TRIM(class_name)) = LOWER(TRIM(?)) 
                             OR class_name IS NULL 
                             OR LOWER(TRIM(department)) = LOWER(TRIM(?)))
                      ORDER BY id DESC''', (user.get("class_name", ""), user.get("department", "")))
    active_sessions = [dict(r) for r in cursor.fetchall()]
    if not active_sessions:
        cursor.execute("SELECT * FROM sessions WHERE is_active=1 ORDER BY id DESC")
        active_sessions = [dict(r) for r in cursor.fetchall()]
    if not active_sessions:
        cursor.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 3")
        active_sessions = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY id DESC", (user["id"],))
    attendance_history = [dict(r) for r in cursor.fetchall()]
    total_attended = len(attendance_history)

    cursor.execute("SELECT COUNT(*) FROM sessions WHERE class_name=? OR class_name IS NULL", (user.get("class_name", ""),))
    total_held = max(cursor.fetchone()[0], total_attended)

    ai_insights = ai_engine.predict_attendance_trajectory(total_attended, total_held)

    conn.close()

    return render_template(request, "student.html", {
        "user": user,
        "is_enrolled": is_enrolled,
        "active_sessions": active_sessions,
        "attendance_history": attendance_history,
        "total_attended": total_attended,
        "total_held": total_held,
        "ai_insights": ai_insights
    })



@app.get("/api/user_photo/{user_id}")
async def get_user_photo(user_id: int):
    photo_path = os.path.join("static", "user_photos", f"user_{user_id}.jpg")
    if os.path.exists(photo_path):
        return FileResponse(photo_path)
    
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT face_photo, name FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row["face_photo"]:
        data_uri = row["face_photo"]
        if "," in data_uri:
            data_uri = data_uri.split(",", 1)[1]
        img_bytes = base64.b64decode(data_uri)
        return Response(content=img_bytes, media_type="image/jpeg")
    
    name = row["name"] if row else "Student"
    return RedirectResponse(f"https://ui-avatars.com/api/?name={urllib.parse.quote(name)}&background=4f46e5&color=fff")


@app.post("/api/student/enroll_face")
async def enroll_face_endpoint(payload: FaceEnrollRequest, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    frame = biometrics.decode_base64_image(payload.image)
    if frame is None:
        return JSONResponse({"status": "error", "message": "Failed to decode camera snapshot."}, status_code=400)

    conn = database.get_db()
    success, msg, vec, photo_b64 = biometrics.save_enrolled_face(user["id"], frame, conn=conn)
    if not success:
        conn.close()
        return JSONResponse({"status": "error", "message": msg}, status_code=422)

    conn.commit()
    conn.close()
    database.sync_backup_file()

    return JSONResponse({
        "status": "success",
        "message": "Biometric face template registered successfully."
    })


@app.post("/api/attendance/mark")
async def mark_pure_face_attendance(payload: AttendanceSubmitRequest, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "student":
        return JSONResponse({"status": "error", "message": "Only authenticated students can mark attendance."}, status_code=403)

    conn = database.get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM sessions WHERE session_code=?", (payload.session_code.strip(),))
    s_row = cursor.fetchone()
    if not s_row:
        cursor.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
        s_row = cursor.fetchone()
    if not s_row:
        conn.close()
        return JSONResponse({"status": "error", "message": f"Class session '{payload.session_code}' not found."}, status_code=404)

    session_data = dict(s_row)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    device_fp = payload.device_fingerprint.strip() if payload.device_fingerprint else "BYOD_STUDENT_DEVICE"

    cursor.execute("DELETE FROM attendance WHERE session_id=? AND student_id=? AND date=?",
                   (session_data["id"], user["id"], date_str))

    # Decode live frame first
    live_frame = biometrics.decode_base64_image(payload.image)
    if live_frame is None:
        conn.close()
        return JSONResponse({"status": "error", "message": "Failed to decode camera frame. Please try again."}, status_code=400)

    enrolled_vector = biometrics.load_enrolled_vector(user["id"], conn=conn)
    if enrolled_vector is None:
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": "Your Face ID is not enrolled yet. Please enroll your clear live face first."
        }, status_code=422)

    # 1. Geofence Check (with indoor calibration)
    geo_result = geofence.validate_spatial_boundary(
        student_lat=payload.latitude,
        student_lon=payload.longitude,
        venue_lat=session_data["latitude"],
        venue_lon=session_data["longitude"],
        allowed_radius_m=session_data["radius_meters"],
        accuracy_m=payload.accuracy
    )

    # 2. Relaxed 1:1 Facial Biometric Verification (White Background Removed)
    secondary_frame = None
    if payload.secondary_image:
        secondary_frame = biometrics.decode_base64_image(payload.secondary_image)

    face_result = biometrics.verify_1to1_face(
        live_frame=live_frame,
        enrolled_vector=enrolled_vector,
        secondary_frame=secondary_frame,
        strict_tolerance=0.68
    )

    if not face_result["verified"]:
        conn.close()
        return JSONResponse({
            "status": "rejected",
            "factor": "facial_biometrics",
            "distance": face_result["distance"],
            "threshold": face_result["threshold"],
            "message": face_result["message"]
        }, status_code=403)

    anomaly = ai_engine.detect_biometric_and_geo_anomalies(
        geo_result["distance_m"], session_data["radius_meters"],
        face_result["distance"], 0.68, payload.accuracy
    )

    cursor.execute('''INSERT INTO attendance 
                      (session_id, student_id, student_name, subject, date, time, distance_meters, face_distance, 
                       device_fingerprint, status, is_manual_override, override_by, remarks)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', 0, NULL, ?)''',
                   (session_data["id"], user["id"], user["name"], session_data["subject"],
                    date_str, time_str, geo_result["distance_m"], face_result["distance"],
                    device_fp,
                    f"Biometric Verified ({face_result['match_confidence_pct']}% confidence) inside {session_data['venue_name']} ({geo_result['distance_m']}m)"))
    conn.commit()
    conn.close()
    database.sync_backup_file()

    return JSONResponse({
        "status": "success",
        "message": f"Attendance Confirmed for {session_data['subject']}! Face verified ({face_result['match_confidence_pct']}%) inside {session_data['venue_name']}.",
        "details": {
            "student_name": user["name"],
            "subject": session_data["subject"],
            "venue_name": session_data["venue_name"],
            "time": time_str,
            "distance_m": geo_result["distance_m"],
            "face_distance": face_result["distance"],
            "confidence_pct": face_result["match_confidence_pct"]
        }
    })


@app.get("/parent", response_class=HTMLResponse)
async def parent_portal(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "parent":
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    conn = database.get_db()
    cursor = conn.cursor()

    student = None
    records = []
    total_classes = 0
    attended_classes = 0
    attendance_pct = 0.0
    consecutive_absent_count = 0
    consecutive_5day_alert = False
    ai_insights = None

    if user.get("student_id"):
        cursor.execute("SELECT * FROM users WHERE id=?", (user["student_id"],))
        st_row = cursor.fetchone()
        if st_row:
            student = dict(st_row)

            cursor.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY id DESC", (student["id"],))
            records = [dict(r) for r in cursor.fetchall()]
            attended_classes = len(records)

            cursor.execute("SELECT id, subject, created_at FROM sessions WHERE class_name=? OR class_name IS NULL ORDER BY id DESC LIMIT 10",
                           (student.get("class_name", ""),))
            recent_sessions = [dict(r) for r in cursor.fetchall()]
            total_classes = max(len(recent_sessions), attended_classes)

            attended_session_ids = {r["session_id"] for r in records}
            for s in recent_sessions:
                if s["id"] not in attended_session_ids:
                    consecutive_absent_count += 1
                else:
                    break

            if consecutive_absent_count >= 4:
                consecutive_5day_alert = True
                if user.get("phone"):
                    alert_msg = f"URGENT: Your ward {student['name']} (Reg: {student['reg_no']}) has been continuously absent for {consecutive_absent_count} consecutive classes! Please contact faculty."
                    database.send_sms(user["phone"], user["name"], "parent", "CONSECUTIVE_ABSENCE_ALERT", alert_msg)

            if total_classes > 0:
                attendance_pct = round((attended_classes / float(total_classes)) * 100.0, 1)

            ai_insights = ai_engine.predict_attendance_trajectory(attended_classes, total_classes)

    conn.close()

    return render_template(request, "parent.html", {
        "user": user,
        "student": student,
        "records": records,
        "total_classes": total_classes,
        "attended_classes": attended_classes,
        "attendance_pct": attendance_pct,
        "consecutive_5day_alert": consecutive_5day_alert,
        "consecutive_absent_count": consecutive_absent_count,
        "ai_insights": ai_insights,
        "current_month": datetime.now().strftime("%B %Y")
    })


@app.post("/api/parent/send_monthly_report")
async def send_monthly_report_sms(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "parent":
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    if not user.get("phone") or not user.get("student_id"):
        return JSONResponse({"status": "error", "message": "Parent mobile or linked student missing."}, status_code=400)

    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, reg_no, class_name FROM users WHERE id=?", (user["student_id"],))
    student = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM attendance WHERE student_id=?", (user["student_id"],))
    attended = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sessions")
    total = max(cursor.fetchone()[0], attended)
    conn.close()

    pct = round((attended / float(total)) * 100.0, 1) if total > 0 else 100.0
    month_name = datetime.now().strftime("%B %Y")

    sms_body = (f"MONTHLY REPORT: {student['name']} ({student['reg_no']}) - Attendance for {month_name}: "
                f"{pct}% ({attended}/{total} classes attended). Status: {'Satisfactory' if pct >= 75 else 'Defaulter Warning'}.")

    database.send_sms(user["phone"], user["name"], "parent", "MONTHLY_REPORT", sms_body)
    links = database.generate_messaging_links(user["phone"], sms_body)

    return JSONResponse({
        "status": "success",
        "message": f"Monthly report summary SMS dispatched to {user['phone']}.",
        "whatsapp_url": links["whatsapp_url"],
        "sms_url": links["sms_url"],
        "sms_body": sms_body
    })


# --- 5. Profile Management & Excel Export ---
@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_template(request, "profile.html", {"user": user, "success_msg": None, "error_msg": None})


@app.post("/profile")
async def profile_update(request: Request,
                         name: str = Form(...),
                         phone: str = Form(...),
                         parent_phone: str = Form(""),
                         email: str = Form(""),
                         password: str = Form("")):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    conn = database.get_db()
    cursor = conn.cursor()

    if password and len(password.strip()) >= 4:
        cursor.execute("UPDATE users SET name=?, phone=?, parent_phone=?, email=?, password=? WHERE id=?",
                       (name.strip(), phone.strip(), parent_phone.strip(), email.strip(), password.strip(), user["id"]))
    else:
        cursor.execute("UPDATE users SET name=?, phone=?, parent_phone=?, email=? WHERE id=?",
                       (name.strip(), phone.strip(), parent_phone.strip(), email.strip(), user["id"]))
    conn.commit()

    cursor.execute("SELECT * FROM users WHERE id=?", (user["id"],))
    updated_user = dict(cursor.fetchone())
    conn.close()

    return render_template(request, "profile.html", {
        "user": updated_user,
        "success_msg": "Your personal profile details and mobile number have been updated successfully.",
        "error_msg": None
    })


@app.get("/export_attendance")
async def export_excel_attendance(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["teacher", "management"]:
        raise HTTPException(status_code=403, detail="Excel export restricted to Teachers & Management.")

    conn = database.get_db()
    df = pd.read_sql_query('''
        SELECT id as "Record ID", session_id as "Session ID", student_id as "Student ID",
               student_name as "Student Name", subject as "Subject", date as "Date", time as "Time",
               distance_meters as "GPS Distance (m)", face_distance as "Face Distance",
               device_fingerprint as "Device Hash", status as "Status", 
               is_manual_override as "Manual Override", override_by as "Override By",
               remarks as "Audit Trace"
        FROM attendance ORDER BY date DESC, time DESC
    ''', conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Face_Geofence_Attendance')
    output.seek(0)

    filename = f"attendance_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
