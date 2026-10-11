import math

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on Earth
    using the Haversine formula.
    
    Args:
        lat1, lon1: Latitude and longitude of point 1 (in decimal degrees)
        lat2, lon2: Latitude and longitude of point 2 (in decimal degrees)
        
    Returns:
        Distance in meters.
    """
    R = 6371000.0  # Earth's mean radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) *
         math.sin(delta_lambda / 2.0) ** 2)
    
    # Avoid numerical instability near extremes
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c


def validate_spatial_boundary(student_lat: float, student_lon: float, 
                             venue_lat: float, venue_lon: float, 
                             allowed_radius_m: float, 
                             accuracy_m: float = 0.0) -> dict:
    """
    Validates whether student device coordinates fall within the authorized physical perimeter.
    
    Returns:
        dict: {
            "valid": bool,
            "distance_m": float,
            "allowed_radius_m": float,
            "accuracy_m": float,
            "message": str
        }
    """
    dist = haversine_distance(student_lat, student_lon, venue_lat, venue_lon)
    
    # Check GPS accuracy tolerance (reject spoofed / coarse cell tower readings > 60m)
    if accuracy_m > 60.0:
        return {
            "valid": False,
            "distance_m": round(dist, 2),
            "allowed_radius_m": allowed_radius_m,
            "accuracy_m": round(accuracy_m, 1),
            "message": f"GPS accuracy error ({accuracy_m:.1f}m) exceeds 60m threshold. Move outdoors or enable high-accuracy location."
        }

    is_within = dist <= allowed_radius_m
    return {
        "valid": is_within,
        "distance_m": round(dist, 2),
        "allowed_radius_m": allowed_radius_m,
        "accuracy_m": round(accuracy_m, 1),
        "message": (
            f"Within perimeter ({dist:.1f}m from center)." 
            if is_within else 
            f"Outside boundary ({dist:.1f}m from center, allowed: {allowed_radius_m:.0f}m)."
        )
    }
