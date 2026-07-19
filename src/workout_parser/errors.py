class WorkoutParserError(Exception):
    """Base class for public workout parser errors."""


class InvalidWorkoutError(WorkoutParserError):
    """Raised when source workout fields contradict one another."""


class UnsupportedWorkoutFeatureError(WorkoutParserError):
    """Raised when strict parsing encounters an unsupported workout construct."""
