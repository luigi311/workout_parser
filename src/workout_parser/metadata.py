_SPORT_NAMES = {
    "run": "running",
    "running": "running",
    "ride": "cycling",
    "cycling": "cycling",
    "bike": "cycling",
    "swim": "swimming",
    "swimming": "swimming",
}


def normalize_sport(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if not normalized:
        return None
    return _SPORT_NAMES.get(normalized, normalized)
