"""Typed pipeline failures. The `code` is stored on the stage/job and shown to users."""


class PipelineError(Exception):
    """A failure with a stable, user-explainable code.

    retryable=False means retrying cannot help (bad input): the job fails immediately instead of
    burning BullMQ attempts. Unknown exceptions are treated as retryable (transient by default).
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class StageCancelled(Exception):
    """Raised at a checkpoint when the user cancelled the job."""


# Input problems: permanent.
MEDIA_CORRUPT = "MEDIA_CORRUPT"
MEDIA_NO_AUDIO = "MEDIA_NO_AUDIO"
MEDIA_TOO_LONG = "MEDIA_TOO_LONG"
UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
SOURCE_MISSING = "SOURCE_MISSING"
# Infrastructure problems: usually transient.
STORAGE_ERROR = "STORAGE_ERROR"
FFMPEG_FAILED = "FFMPEG_FAILED"
INTERNAL_ERROR = "INTERNAL_ERROR"


def classify(exc: BaseException) -> PipelineError:
    if isinstance(exc, PipelineError):
        return exc
    name = type(exc).__name__
    if name in {"ClientError", "EndpointConnectionError", "ReadTimeoutError"}:
        return PipelineError(STORAGE_ERROR, f"{name}: {exc}", retryable=True)
    return PipelineError(INTERNAL_ERROR, f"{name}: {exc}", retryable=True)
