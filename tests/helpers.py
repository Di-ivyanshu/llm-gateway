"""Fakes shared by the test modules — no network, no keys, no Redis."""
from __future__ import annotations


class FakeResponse:
    """Shape of a litellm.completion result (object-style access)."""

    def __init__(self, content: str = "hello", tokens: tuple[int, int, int] = (3, 5, 8)):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})})]
        prompt, completion, total = tokens
        self.usage = type("U", (), {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        })


def fails(name: str = "InternalServerError", message: str = "down") -> Exception:
    """An exception instance whose class name `errors.classify` understands."""
    return type(name, (Exception,), {})(message)


def always_failing(exc: Exception | None = None):
    """A `_completion` stub that fails every call."""

    def stub(**_kwargs):
        raise exc or fails()

    return stub


def by_model(mapping: dict, default=None):
    """A `_completion` stub that behaves differently per provider model id.

    Values may be an exception instance (raised) or a zero-arg callable
    returning a response.
    """

    def stub(**kwargs):
        action = mapping.get(kwargs["model"], default)
        if action is None:
            return FakeResponse()
        if isinstance(action, BaseException):
            raise action
        return action()

    return stub
