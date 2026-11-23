from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_response

MEDIA_PATHS = {
    "/api/v1/translations/sign-to-text",
    "/api/v1/translations/sign-to-speech",
    "/api/v1/translations/speech-to-text",
}
JSON_LIMIT = 32 * 1024


class SmallBodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}
                or scope["path"].rstrip("/") in MEDIA_PATHS):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > JSON_LIMIT:
                await error_response(413, "Request body exceeds the size limit")(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
