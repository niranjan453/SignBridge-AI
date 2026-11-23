from fastapi.responses import JSONResponse


def error_response(status: int, message: str, details=None, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"message": message, "details": details},
    }, headers=headers)
