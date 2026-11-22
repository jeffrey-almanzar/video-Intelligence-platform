"""UUIDv7 (RFC 9562): time-ordered ids, matching what the NestJS side generates.

Python 3.12's `uuid` module has no uuid7 (added in 3.14), and it's small enough not to warrant
a dependency.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits; 74 are used
    value = (unix_ms & ((1 << 48) - 1)) << 80  # 48-bit timestamp
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & 0xFFF) << 64  # 12 bits rand_a
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & ((1 << 62) - 1)  # 62 bits rand_b
    return uuid.UUID(int=value)
