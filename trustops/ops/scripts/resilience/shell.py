"""Running local commands with a deadline, behind a seam tests can fake."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional, Protocol

from resilience.errors import CommandError, CommandTimeoutError

logger = logging.getLogger(__name__)

#: How much of a failed command's output goes into the error message.
_TAIL_CHARS = 600


@dataclass(frozen=True)
class CommandResult:
    """What one command printed and how it ended.

    Attributes:
        argv: The command line that ran.
        returncode: The exit status.
        stdout: Standard output, decoded.
        stderr: Standard error, decoded.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs one command; :class:`SubprocessRunner` in production."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> CommandResult:
        """Run ``argv`` and return its result.

        Args:
            argv: The command and its arguments (never a shell string).
            timeout_s: Deadline in seconds; the command is killed past it.
            input_text: Text for the command's standard input.
            check: Raise :class:`CommandError` on a non-zero exit.
        """
        ...


def describe_failure(result: CommandResult) -> str:
    """One line saying what ran and the tail of what it said."""
    said = (result.stderr or result.stdout).strip()
    tail = said[-_TAIL_CHARS:] if said else "no output"
    return f"`{' '.join(result.argv[:4])} ...` exited {result.returncode}: {tail}"


class SubprocessRunner:
    """:class:`CommandRunner` over :func:`subprocess.run`."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> CommandResult:
        """Run ``argv`` with a deadline.

        Raises:
            CommandTimeoutError: the command ran past ``timeout_s``.
            CommandError: the program could not start, or exited non-zero
                while ``check`` is true.
        """
        command = tuple(argv)
        try:
            proc = subprocess.run(  # noqa: S603 - argv is built by this package
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(
                f"`{' '.join(command[:4])} ...` timed out after {timeout_s:g}s"
            ) from exc
        except OSError as exc:
            raise CommandError(f"cannot run `{command[0]}`: {exc}") from exc
        result = CommandResult(command, proc.returncode, proc.stdout, proc.stderr)
        if check and result.returncode != 0:
            raise CommandError(describe_failure(result))
        return result
