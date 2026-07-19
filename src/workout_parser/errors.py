class WorkoutParserError(Exception):
    """Base class for public workout parser errors."""


class WorkoutFileError(WorkoutParserError):
    """Raised when a workout path cannot be read as a regular file."""


class UnsupportedFormatError(WorkoutParserError):
    """Raised when a workout file format is not supported."""


class InvalidWorkoutError(WorkoutParserError):
    """Raised when source workout fields contradict one another."""


class UnsupportedWorkoutFeatureError(WorkoutParserError):
    """Raised when strict parsing encounters an unsupported workout construct."""
