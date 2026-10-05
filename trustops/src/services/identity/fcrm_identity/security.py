"""Password verification for X1 login (design A §8.1).

Fix carried from the cloned ``evoqidentityservice`` (CLAUDE.md's AEO-reuse
table, bug 2): its ``CustomAuthService.processOTPLogin`` logged the
plaintext OTP at ``INFO`` on every request (``"Email will be sent to
{user_email} with OTP {otp} and request_id {request_id}"``). Design A
§8.1 fix 2 removes the OTP flow entirely rather than patch that one log
line — password login replaces it (``fcrm_contracts.api.identity.LoginRequest``
has no OTP field) — so there is no OTP anywhere in this service to log.

The same discipline carries over to the password that replaces it:
nothing in this module, or in ``routes.py``'s one caller
(:func:`login`), ever passes the plaintext password to ``logging`` —
not in a message, and not under an ``extra=`` key (which
``fcrm_common.logging``'s ``redact()`` would drop anyway, since it
matches `password` by name; this module simply never gives it the
chance). ``test_upstream_fixes.py::test_otp_never_appears_in_logs`` asserts
this by capturing every log record a login attempt produces.
"""

from __future__ import annotations

import bcrypt

# A bcrypt hash of a fixed, non-secret placeholder. Used when no real user
# record exists, so ``verify_password`` always does the same amount of work
# whether the account is missing or the password is merely wrong -- an
# unknown login name and a wrong password must look identical from the
# outside.
DUMMY_HASH = bcrypt.hashpw(
    b"fcrm-identity-dummy-hash-no-such-account", bcrypt.gensalt()
).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` matches the bcrypt digest ``password_hash``.

    Args:
        password: The plaintext value the caller presented. Never logged.
        password_hash: A bcrypt hash (``$2b$...``), e.g.
            ``UserRecord.password_hash`` or :data:`DUMMY_HASH`.

    Returns:
        ``False`` for a malformed hash rather than raising, so a corrupt
        stored value fails a login instead of 500ing the request.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
