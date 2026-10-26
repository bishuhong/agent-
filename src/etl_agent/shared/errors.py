"""Framework-independent, safe application failures."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FieldError:
    path: str
    code: str
    message: str


class ApplicationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        field_errors: list[FieldError] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.field_errors = field_errors or []
        self.retryable = retryable

    def safe_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field_errors": [asdict(item) for item in self.field_errors],
            "retryable": self.retryable,
        }


def unavailable(component: str) -> ApplicationError:
    return ApplicationError(
        "DEPENDENCY_UNAVAILABLE", f"{component}未配置或尚未验证其运行能力", 503, retryable=True
    )


def invalid(message: str, path: str = "") -> ApplicationError:
    fields = [FieldError(path, "invalid", message)] if path else []
    return ApplicationError("VALIDATION_FAILED", message, 422, fields)


def forbidden(message: str = "当前主体没有该项目操作权限") -> ApplicationError:
    return ApplicationError("FORBIDDEN", message, 403)
