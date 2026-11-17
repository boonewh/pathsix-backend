"""Operator database scripts accept only their exact app, host, database and user."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from database_targets import TARGETS, checked_admin_url  # noqa: E402

PROD = 'postgresql://pathsixsolutions_backend:x@pathsixsolutions-db.flycast:5432/pathsixsolutions_backend'
REHEARSAL = 'postgres://pathsixsolutions_backend:x@pathsixsolutions-db-rehearsal.flycast:5432/pathsixsolutions_backend'


def test_each_target_accepts_only_its_own_database():
    assert checked_admin_url('production', PROD, 'pathsixsolutions-backend').host == 'pathsixsolutions-db.flycast'
    assert checked_admin_url('rehearsal', REHEARSAL, 'pathsixsolutions-backend-staging').database == 'pathsixsolutions_backend'
    refused = [
        ('production', PROD, 'pathsixsolutions-backend-staging'),       # wrong machine
        ('production', REHEARSAL, 'pathsixsolutions-backend'),         # the rehearsal copy is not production
        ('rehearsal', PROD, 'pathsixsolutions-backend-staging'),       # production is not the rehearsal copy
        ('production', PROD.replace('pathsixsolutions_backend:', 'pathsix_crm_runtime:'), 'pathsixsolutions-backend'),
        ('production', PROD.replace('/pathsixsolutions_backend', '/other'), 'pathsixsolutions-backend'),
        ('staging', PROD, 'pathsixsolutions-backend-staging'),
    ]
    for name, url, app in refused:
        with pytest.raises(RuntimeError):
            checked_admin_url(name, url, app)
    with pytest.raises(RuntimeError):
        checked_admin_url('anywhere', PROD, 'pathsixsolutions-backend')


def test_rehearsal_mirrors_production_and_staging_is_unchanged():
    for key in ('database', 'admin_user', 'runtime_role'):
        assert TARGETS['rehearsal'][key] == TARGETS['production'][key]
    assert TARGETS['staging']['runtime_role'] == 'pathsix_crm_staging_runtime'
    assert not TARGETS['rehearsal']['hosts'] & TARGETS['production']['hosts']
