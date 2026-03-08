from datetime import date
from workout_parser.intervals_icu import parse_intervals_icu_json_file
from workout_parser.fit import parse_fit
from workout_parser.models import Workout
from pathlib import Path
import re

SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "vs",
}
ACRONYM_MAP = {
    "ftp": "FTP",
    "hr": "HR",
    "bpm": "BPM",
    "vo2": "VO2",
    "vo2max": "VO2max",
}

_TIME_RE = re.compile(r"^(?P<m>\d+):(?P<s>\d{1,2})(?:\.(?P<ms>\d+))?$")


def pretty_workout_name(raw: str) -> str:
    """
    Turn a file-ish workout name into a human-friendly title:
      - replace underscores/dashes with spaces
      - collapse spaces
      - Title Case with small-words lowercased (except if first)
      - preserve common acronyms (FTP, HR, BPM, VO2, VO2max).
    """
    s = (raw or "").strip()
    s = re.sub(r"[_\-]+", " ", s)  # underscores/dashes -> spaces
    s = re.sub(r"\s+", " ", s)  # collapse whitespace
    if not s:
        return "Workout"

    words = s.split(" ")
    out: list[str] = []
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in ACRONYM_MAP:
            out.append(ACRONYM_MAP[wl])
        elif i > 0 and wl in SMALL_WORDS:
            out.append(wl)
        else:
            # Capitalize first char, keep rest as-is (handles numbers nicely)
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)

def load_workout(path: Path) -> Workout:
    ext = path.suffix.lower()
    if ext == ".fit":
        return parse_fit(path)
    if ext == ".json":
        return parse_intervals_icu_json_file(path)
    return Workout(name=path.stem, steps=[])


# -----------------------
# Discovery
# -----------------------

SUPPORTED_EXTS = (".fit", ".json")
AUTO_SUBDIRS = ("intervals_icu",)


def _date_from_filename(p: Path) -> date | None:
    # YYYY-MM-DD Title.ext
    try:
        return date.fromisoformat(p.stem.split(" ", 1)[0])
    except Exception:
        return None


def discover_workouts(running_dir: Path) -> list[Path]:
    """
    Return workout files in the order:
      1) Today's dated auto files
      2) Other dated auto files later this week (ascending date)
      3) Manual files in the root 'running' directory
    """
    today = date.today()

    # Collect auto files from provider subfolders
    auto_files: list[Path] = []
    for sub in AUTO_SUBDIRS:
        d = running_dir / sub
        if d.is_dir():
            auto_files.extend([p for p in d.glob("*.*") if p.is_file()])

    # Partition autos by date
    todays: list[tuple[date, Path]] = []
    weeks: list[tuple[date, Path]] = []
    for p in auto_files:
        d = _date_from_filename(p)
        if not d:
            continue
        if d == today:
            todays.append((d, p))
        elif 0 <= (d - today).days <= 6:
            weeks.append((d, p))

    todays.sort(key=lambda t: t[0])  # single day but deterministic
    weeks.sort(key=lambda t: t[0])  # ascending date

    # Manual files live in running_dir root (ignore provider subdirs)
    manual = sorted(
        [p for p in running_dir.glob("*.*") if p.is_file()],
        key=lambda p: p.stem.lower(),
    )

    # Stitch in order
    ordered = [p for _, p in todays] + [p for _, p in weeks] + manual
    return ordered
