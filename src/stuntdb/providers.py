"""Stable, explicit format-preserving providers; no remote or random services."""
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

PROVIDERS = {'email', 'phone', 'name', 'date', 'integer', 'number'}


def validate_provider(action, columns):
    if action in {'email', 'phone', 'name'}:
        if any(not isinstance(c.type, sa.String) or isinstance(c.type, sa.Enum) or hasattr(c.type, 'values') for c in columns):
            raise ValueError('String provider requires ordinary string columns')
    elif action == 'date':
        if any(not isinstance(c.type, sa.Date) or isinstance(c.type, sa.DateTime) for c in columns):
            raise ValueError('Date provider requires DATE columns')
    elif action == 'integer':
        if any(not isinstance(c.type, sa.Integer) for c in columns):
            raise ValueError('Integer provider requires integer columns')
    elif action == 'number':
        if any(not isinstance(c.type, sa.Numeric) or isinstance(c.type, sa.Float) or c.type.precision is None or c.type.scale is None for c in columns):
            raise ValueError('Number provider requires fixed precision DECIMAL columns')
        if len({(c.type.precision, c.type.scale) for c in columns}) != 1:
            raise ValueError('Linked decimal definitions must agree')


def _integer_bounds(columns):
    bounds = []
    for c in columns:
        name = c.type.__class__.__name__.upper()
        bits = {'TINYINT': 8, 'SMALLINT': 16, 'SMALLINTEGER': 16,
                'MEDIUMINT': 24, 'BIGINT': 64, 'BIGINTEGER': 64}.get(name, 32)
        unsigned = bool(getattr(c.type, 'unsigned', False))
        bounds.append((0, 2 ** bits - 1) if unsigned else (-2 ** (bits - 1), 2 ** (bits - 1) - 1))
    return max(b[0] for b in bounds), min(b[1] for b in bounds)


def _magnitude(value, random_value, bounds):
    digits = len(str(abs(value)))
    lower = 10 ** (digits - 1) if digits > 1 else 0
    if value != 0:
        lower = max(lower, 1)
    upper = min(10 ** digits - 1, bounds[1] if value >= 0 else -bounds[0])
    if upper < lower or (value < 0 and bounds[0] >= 0):
        raise ValueError('Value cannot fit linked numeric columns')
    magnitude = lower + random_value % (upper - lower + 1)
    return -magnitude if value < 0 else magnitude


def provide(action, value, salt, domain, columns, length):
    # Empty optional fields contain no identifier and must remain empty.
    if action in {'email', 'phone', 'name'} and value == '':
        return ''
    canonical = value.isoformat() if isinstance(value, date) else str(value)
    payload = json.dumps([action, domain, canonical], ensure_ascii=False, separators=(',', ':')).encode()
    digest = hmac.new(salt.encode(), payload, hashlib.sha256).digest()
    random_value = int.from_bytes(digest, 'big')
    if action == 'email':
        if not isinstance(value, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Unsupported email shape')
        suffix = '@example.invalid'
        size = min(32, length - len(suffix))
        if size < 1:
            raise ValueError('Email column is too short')
        return digest.hex()[:size] + suffix
    if action in {'phone', 'name'}:
        if not isinstance(value, str) or len(value) > length:
            raise ValueError('String does not fit linked columns')
        if action == 'phone' and (not any(c.isdigit() for c in value) or not re.fullmatch(r'[0-9+(). \-]+', value)):
            raise ValueError('Unsupported phone shape')
        if action == 'name' and (not any(c.isalpha() for c in value) or any(not (c.isalpha() or c.isspace() or c in "'-.") for c in value)):
            raise ValueError('Unsupported name shape')
        stream = b''.join(hmac.new(salt.encode(), digest + i.to_bytes(4, 'big'), hashlib.sha256).digest()
                          for i in range((len(value) + 31) // 32))
        output = []
        for i, c in enumerate(value):
            if c.isdigit():
                output.append(str(stream[i] % 10))
            elif action == 'name' and c.isalpha():
                letter = chr(ord('a') + stream[i] % 26)
                output.append(letter.upper() if c.isupper() else letter)
            else:
                output.append(c)
        return ''.join(output)
    if action == 'date':
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError('Unsupported date value')
        start = date(1940, 1, 1)
        return start + timedelta(days=random_value % ((date(2005, 12, 31) - start).days + 1))
    if action == 'integer':
        if type(value) is not int:
            raise ValueError('Unsupported integer value')
        return _magnitude(value, random_value, _integer_bounds(columns))
    if action == 'number':
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError('Unsupported decimal value')
        scale = columns[0].type.scale
        precision = columns[0].type.precision
        # Use integer tuple arithmetic to avoid decimal-context rounding.
        sign, digits, exponent = value.as_tuple()
        units = int(''.join(map(str, digits))) * (-1 if sign else 1)
        shift = exponent + scale
        if shift >= 0:
            units *= 10 ** shift
        elif units % (10 ** -shift):
            raise ValueError('Decimal scale mismatch')
        else:
            units //= 10 ** -shift
        maximum = 10 ** precision - 1
        minimum = 0 if any(getattr(c.type, 'unsigned', False) for c in columns) else -maximum
        result = _magnitude(units, random_value, (minimum, maximum))
        return Decimal((int(result < 0), tuple(map(int, str(abs(result)))), -scale))
    raise ValueError('Unknown provider')
