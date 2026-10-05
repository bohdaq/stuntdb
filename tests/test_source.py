import pytest

from stuntdb.source import engine_for, validate_server


@pytest.mark.parametrize('version,family', [
    ('8.0.45', 'mysql'), ('8.4.8', 'mysql'),
    ('10.11.14-MariaDB-ubu2204', 'mariadb'), ('11.4.10-MariaDB-ubu2404', 'mariadb'),
    ('5.5.5-10.11.14-MariaDB', 'mariadb')])
def test_supported_servers(version, family):
    assert validate_server(version) == family


@pytest.mark.parametrize('version', ['5.7.44', '9.0.0', '10.6.24-MariaDB', '11.8.1-MariaDB', 'unknown-MariaDB'])
def test_untested_servers_rejected(version):
    with pytest.raises(ValueError):
        validate_server(version)


@pytest.mark.parametrize('scheme', ['mysql', 'mysql+pymysql', 'mariadb', 'mariadb+pymysql'])
def test_server_urls_use_pymysql(scheme):
    engine = engine_for(f'{scheme}://user:password@localhost/stuntdb_test')
    assert engine.url.drivername == 'mysql+pymysql'
    engine.dispose()
