import math
from typing import Dict, Any, List

def calculate_attendance_recovery(attended: int, total: int, target_pct: float = 75.0) -> int:
    """
    Calculates the minimum number of consecutive classes a student must attend
    to recover and reach the target attendance percentage (default 75%).
    
    Formula: (attended + x) / (total + x) >= target / 100
    x >= (target * total - 100 * attended) / (100 - target)
    """
    if total == 0:
        return 0
    current_pct = (attended / total) * 100.0
    if current_pct >= target_pct:
        return 0
    
    target_ratio = target_pct / 100.0
    numerator = (target_ratio * total) - attended
    denominator = 1.0 - target_ratio
    
    if denominator <= 0:
        return 0
    
    required = math.ceil(numerator / denominator)
    return max(0, required)


def predict_attendance_trajectory(attended: int, total_held: int, total_semester_classes: int = 50) -> Dict[str, Any]:
    """
    AI Predictive Model:
    Projects end-of-semester attendance percentage based on current habit trajectory
    and computes risk level (LOW, MEDIUM, HIGH/CRITICAL).
    """
    if total_held == 0:
        return {
            "current_pct": 100.0,
            "projected_pct": 100.0,
            "risk_level": "LOW",
            "risk_color": "emerald",
            "consecutive_needed": 0,
            "can_miss_max": math.floor(total_semester_classes * 0.25),
            "recommendation": "Semester beginning. Maintain regular attendance above 75%."
        }
    
    current_pct = round((attended / total_held) * 100.0, 1)
    current_rate = attended / float(total_held)
    
    remaining_classes = max(0, total_semester_classes - total_held)
    projected_attended = attended + round(remaining_classes * current_rate)
    projected_pct = round((projected_attended / float(total_semester_classes)) * 100.0, 1)
    
    consecutive_needed = calculate_attendance_recovery(attended, total_held, 75.0)
    
    # Calculate how many more classes student can afford to miss
    # (attended + 0) / (total_held + remaining) >= 0.75
    max_total_absences_allowed = math.floor(total_semester_classes * 0.25)
    current_absences = total_held - attended
    remaining_absences_cushion = max(0, max_total_absences_allowed - current_absences)
    
    if current_pct < 65.0:
        risk_level = "CRITICAL DEFAULTER"
        risk_color = "rose"
        rec = f"Critical attendance shortage ({current_pct}%). Student must attend the next {consecutive_needed} consecutive classes without missing any to regain exam eligibility."
    elif current_pct < 75.0:
        risk_level = "MODERATE RISK"
        risk_color = "amber"
        rec = f"Below 75% minimum threshold ({current_pct}%). Need {consecutive_needed} consecutive attendances to safely surpass the 75% benchmark."
    elif current_pct < 85.0:
        risk_level = "SAFE MARGIN"
        risk_color = "cyan"
        rec = f"Satisfactory attendance ({current_pct}%). Can afford to miss at most {remaining_absences_cushion} more lectures this term."
    else:
        risk_level = "EXEMPLARY"
        risk_color = "emerald"
        rec = f"Excellent attendance record ({current_pct}%). Projected to finish semester with {projected_pct}%."
        
    return {
        "current_pct": current_pct,
        "projected_pct": projected_pct,
        "risk_level": risk_level,
        "risk_color": risk_color,
        "consecutive_needed": consecutive_needed,
        "remaining_absences_cushion": remaining_absences_cushion,
        "recommendation": rec
    }


def detect_biometric_and_geo_anomalies(distance_m: float, allowed_radius_m: float, 
                                       face_distance: float, face_threshold: float = 0.50, 
                                       accuracy_m: float = 10.0) -> Dict[str, Any]:
    """
    AI Anomaly Detection Engine:
    Evaluates check-in signals to detect potential proxy attempts, GPS jitter, or boundary edge cases.
    """
    flags = []
    anomaly_score = 0.0  # 0.0 (Safe) to 1.0 (Highly Suspicious)
    
    # Check 1: Perimeter edge proximity (> 90% of boundary radius)
    if distance_m > (allowed_radius_m * 0.88):
        flags.append("Boundary Edge Warning: Student verified near outer perimeter limit.")
        anomaly_score += 0.25
        
    # Check 2: Borderline facial match (distance close to 0.50)
    if face_distance > 0.44:
        flags.append("Borderline Biometric Match: Facial vector distance is close to rejection threshold.")
        anomaly_score += 0.35
        
    # Check 3: Low GPS satellite accuracy (> 30m)
    if accuracy_m > 30.0:
        flags.append("High GPS Uncertainty: Circular error probable is elevated.")
        anomaly_score += 0.20
        
    is_suspicious = anomaly_score >= 0.50
    return {
        "is_suspicious": is_suspicious,
        "anomaly_score": round(anomaly_score, 2),
        "confidence_level": "VERIFIED_AUTHENTIC" if not is_suspicious else "FLAGGED_FOR_AUDIT",
        "flags": flags
    }
