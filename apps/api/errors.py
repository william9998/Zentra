"""全平台唯一的錯誤格式 —— api-contract.md §0。

後端不准自創回應格式：一律 raise ZentraError。
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.enums import ErrorCode

# EGRESS_BLOCKED 刻意**不在**這裡：政策不會因為再打一次就改變。
# EGRESS_FAILED 在：那是上游打不通，重試有意義（api-contract §0）。
_RETRYABLE = {ErrorCode.QUOTA_EXCEEDED, ErrorCode.SANDBOX_TIMEOUT, ErrorCode.SANDBOX_ERROR,
              ErrorCode.LLM_ERROR, ErrorCode.EGRESS_FAILED, ErrorCode.INTERNAL_ERROR}


class ZentraError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        http_status: int = 400,
        run_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.run_id = run_id
        self.details = details or {}

    def body(self) -> dict:
        return {
            "error": {
                "code": str(self.code),
                "message": self.message,
                "run_id": self.run_id,
                "retryable": self.code in _RETRYABLE,
                "details": self.details,
            }
        }


async def zentra_error_handler(_: Request, exc: ZentraError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.body())


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Avoid echoing input, credentials, or non-JSON ValueError objects.
    details = {"fields": [
        {"loc": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]}
    error = ZentraError(ErrorCode.VALIDATION_ERROR, "request validation failed", 422, details=details)
    return JSONResponse(status_code=422, content=error.body())


async def internal_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error = ZentraError(ErrorCode.INTERNAL_ERROR, "internal service temporarily unavailable", 500)
    return JSONResponse(status_code=500, content=error.body())


def not_found(what: str) -> ZentraError:
    return ZentraError(ErrorCode.NOT_FOUND, f"{what} not found", 404)
