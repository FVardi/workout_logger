"""Password hashing, kept separate from app.py so the hashing scheme can be
swapped (or the work factor tuned) without touching any route.

Calls the `bcrypt` package directly - deliberately NOT via passlib.
passlib 1.7.4 (unmaintained since 2020) is broken with bcrypt>=4.1: its
CryptContext runs an internal self-test on first use (`detect_wrap_bug`,
checking for a historical bcrypt wraparound bug) that itself feeds bcrypt a
too-long test string, and bcrypt>=4.1 raises instead of the older silent-
truncate behavior that self-test relied on. The result is that passlib
raises `ValueError: password cannot be longer than 72 bytes...` on the
*first* hash or verify call regardless of the actual password - not
something a length check on our own password can work around, since the
crash happens before passlib ever looks at it. Reported upstream; passlib
has no fix and isn't expected to get one. Calling bcrypt directly sidesteps
passlib's self-test entirely and is barely more code.
"""

import bcrypt

# bcrypt only ever uses a password's first 72 *bytes* - a property of the
# algorithm itself. Enforced explicitly so a too-long password is a clean
# rejection instead of a crash inside bcrypt.hashpw()/checkpw().
MAX_PASSWORD_BYTES = 72


def password_too_long(password: str) -> bool:
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    if password_too_long(password):
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if password_too_long(password):
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
