"""Error types. Each carries the exit code and the JSON shape the CLI prints."""

from __future__ import annotations


class LinShareError(Exception):
    code = "error"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        linshare_error_code: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.http_status = http_status
        self.linshare_error_code = linshare_error_code

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "httpStatus": self.http_status,
                "linshareErrorCode": self.linshare_error_code,
            }
        }


class UsageError(LinShareError):
    """Bad input given to the CLI (exit 1)."""

    code = "usage"
    exit_code = 1


class AuthError(LinShareError):
    """Not logged in, token rejected, 2FA needed, unsupported server (exit 2)."""

    code = "auth"
    exit_code = 2


class BusinessError(LinShareError):
    """The server refused the operation (exit 3)."""

    code = "refused"
    exit_code = 3


class ServerError(LinShareError):
    """Network failure or server-side error (exit 4)."""

    code = "server"
    exit_code = 4


# LinShare BusinessErrorCode values (linshare-core BusinessErrorCode.java) → plain sentences.
_PLAIN_MESSAGES = {
    3002: "This type of file is not allowed on your LinShare server.",
    5400: "The share has no recipients.",
    5401: "The expiry date is not allowed by your LinShare settings (too far or too close).",
    30003: "The total size of the files is above the limit of this upload request.",
    30004: "A file is above the size limit of this upload request.",
    33423: "Your account is not allowed to share with people outside your organisation (anonymous shares).",
    46010: "The file is larger than the maximum file size allowed on your LinShare account.",
    46011: "Your LinShare space is full: delete some documents or ask your administrator for more quota.",
    46012: "Your LinShare space is full: delete some documents or ask your administrator for more quota.",
    46013: "Your organisation's LinShare space is full: contact your administrator.",
    46014: "The LinShare server is out of space: contact your administrator.",
    53004: "Your LinShare account is not allowed to create long-lived tokens: "
    "ask your administrator to enable the permanent token (JWT) functionality for your domain.",
    53005: "Your LinShare account is not allowed to use long-lived tokens: "
    "ask your administrator to enable the permanent token (JWT) functionality for your domain.",
}


def plain_message(err_code: int, server_message: str) -> str:
    """Plain sentence for common LinShare business errors, else the server's message."""
    return _PLAIN_MESSAGES.get(err_code, server_message)
