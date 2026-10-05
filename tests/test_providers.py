from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from hypothesis import given, strategies as st
from sqlalchemy.dialects import mysql

from stuntdb.core import Slice, verify
from stuntdb.masking import mask_slice, MaskingError, LeakError
from stuntdb.providers import provide, validate_provider

SALT = 'test-only-project-salt-1234567890'


def result(action, value, kind):
    m = sa.MetaData()
    sa.Table('person', m, sa.Column('id', sa.Integer, primary_key=True), sa.Column('value', kind))
    original = Slice(m, {'person': [{'id': 1, 'value': value}]})
    return mask_slice(original, SALT, {'person.value': action})


@pytest.mark.parametrize('action,value,kind', [
    ('email', 'canary@example.org', sa.String(100)),
    ('phone', '+48 (123) 456-789', sa.String(30)),
    ('name', "Zoë O'Reilly", sa.String(100)),
    ('date', date(1960, 2, 29), sa.Date()),
    ('integer', -123456, sa.Integer()),
    ('number', Decimal('-123.45'), sa.Numeric(8, 2)),
])
def test_provider_determinism_and_no_original(action, value, kind):
    one = result(action, value, kind)
    assert one.rows == result(action, value, kind).rows
    assert one.rows['person'][0]['value'] != value


def test_email_domain_and_narrow_column():
    email = result('email', 'canary@example.org', sa.String(20)).rows['person'][0]['value']
    assert email.endswith('@example.invalid') and len(email) <= 20
    with pytest.raises(MaskingError):
        result('email', 'canary@example.org', sa.String(10))


def test_phone_and_name_shapes():
    phone = '+48 (123) 456-789'
    masked = result('phone', phone, sa.String(30)).rows['person'][0]['value']
    assert len(masked) == len(phone)
    assert [(i, c) for i, c in enumerate(phone) if not c.isdigit()] == [(i, c) for i, c in enumerate(masked) if not c.isdigit()]
    name = "Zoë O'Reilly"
    masked = result('name', name, sa.String(50)).rows['person'][0]['value']
    assert len(masked) == len(name)
    assert [c.isupper() for c in masked] == [c.isupper() for c in name]
    assert [c for c in masked if not c.isalpha()] == [c for c in name if not c.isalpha()]


@pytest.mark.parametrize('action,value,kind', [
    ('email', 'not an email', sa.String(100)), ('phone', 'ext: abc', sa.String(100)),
    ('name', '123', sa.String(100)), ('date', '1960-01-01', sa.String(100)),
    ('integer', '123', sa.String(100)), ('number', Decimal('12.3'), sa.Float()),
])
def test_invalid_provider_inputs_fail(action, value, kind):
    with pytest.raises(MaskingError):
        result(action, value, kind)


def test_linked_email_domains_with_different_widths():
    m = sa.MetaData()
    sa.Table('parent', m, sa.Column('email', sa.String(100), primary_key=True))
    sa.Table('child', m, sa.Column('id', sa.Integer, primary_key=True), sa.Column('email', sa.String(25), sa.ForeignKey('parent.email')))
    original = Slice(m, {'parent': [{'email': 'canary@example.org'}], 'child': [{'id': 1, 'email': 'canary@example.org'}]})
    masked = mask_slice(original, SALT, {'parent.email': 'email'})
    assert masked.rows['parent'][0]['email'] == masked.rows['child'][0]['email']
    assert len(masked.rows['parent'][0]['email']) <= 25
    verify(masked)


@given(st.integers(min_value=-128, max_value=127))
def test_integer_provider_respects_tinyint_and_width(value):
    column = sa.Column('value', mysql.TINYINT())
    masked = provide('integer', value, SALT, 'test.value', [column], 128)
    assert -128 <= masked <= 127
    assert len(str(abs(masked))) == len(str(abs(value)))
    assert masked >= 0 if value >= 0 else masked <= 0


@given(st.integers(min_value=1, max_value=999999))
def test_decimal_scale_and_magnitude(units):
    value = Decimal((0, tuple(map(int, str(units))), -2))
    column = sa.Column('value', sa.Numeric(6, 2))
    masked = provide('number', value, SALT, 'test.value', [column], 128)
    assert masked.as_tuple().exponent == -2
    assert masked >= 0 and masked <= Decimal('9999.99')
    assert len(masked.as_tuple().digits) == len(value.as_tuple().digits)


def test_large_decimal_avoids_context_rounding():
    value = Decimal('123456789012345678901234567890123456789012345678901234567890.12345')
    column = sa.Column('value', mysql.DECIMAL(65, 5))
    masked = provide('number', value, SALT, 'test.value', [column], 128)
    assert len(masked.as_tuple().digits) == 65
    assert masked.as_tuple().exponent == -5


def test_dates_share_relationship_domain():
    m = sa.MetaData()
    sa.Table('parent', m, sa.Column('day', sa.Date, primary_key=True))
    sa.Table('child', m, sa.Column('id', sa.Integer, primary_key=True), sa.Column('day', sa.Date, sa.ForeignKey('parent.day')))
    day = date(1960, 2, 29)
    masked = mask_slice(Slice(m, {'parent': [{'day': day}], 'child': [{'id': 1, 'day': day}]}), SALT, {'parent.day': 'date'})
    assert masked.rows['parent'][0]['day'] == masked.rows['child'][0]['day']
    assert date(1940, 1, 1) <= masked.rows['parent'][0]['day'] <= date(2005, 12, 31)


def test_provider_null_preservation():
    assert result('email', None, sa.String(100)).rows['person'][0]['value'] is None
    assert result('date', None, sa.Date()).rows['person'][0]['value'] is None
