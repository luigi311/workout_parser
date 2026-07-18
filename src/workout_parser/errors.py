class WorkoutParserError(Exception):
    """Base class for public workout parser errors."""


class UnsupportedWorkoutFeatureError(WorkoutParserError):
    """Raised when strict parsing encounters an unsupported workout construct."""
