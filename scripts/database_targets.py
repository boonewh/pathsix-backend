"""Exact database targets for operator scripts. Anything else is refused.

Each target names the Fly app the script must run on, the database it may
touch, the administrator login and the restricted runtime role. The rehearsal
target is a private copy restored from a production snapshot: it uses
production's database, user and role names so the rehearsal matches the real
run, and runs from the staging machine, which carries the new code.
"""
from sqlalchemy.engine import make_url

TARGETS = {
    'staging': {
        'app': 'pathsixsolutions-backend-staging',
        'hosts': {'pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'},
        'database': 'pathsixsolutions_backend_staging',
        'admin_user': 'pathsixsolutions_backend_staging',
        'runtime_role': 'pathsix_crm_staging_runtime',
    },
    'rehearsal': {
        'app': 'pathsixsolutions-backend-staging',
        'hosts': {'pathsixsolutions-db-rehearsal.flycast', 'pathsixsolutions-db-rehearsal.internal'},
        'database': 'pathsixsolutions_backend',
        'admin_user': 'pathsixsolutions_backend',
        'runtime_role': 'pathsix_crm_runtime',
    },
    'production': {
        'app': 'pathsixsolutions-backend',
        'hosts': {'pathsixsolutions-db.flycast', 'pathsixsolutions-db.internal'},
        'database': 'pathsixsolutions_backend',
        'admin_user': 'pathsixsolutions_backend',
        'runtime_role': 'pathsix_crm_runtime',
    },
}


def target(name):
    if name not in TARGETS:
        raise RuntimeError('Unknown database target')
    return TARGETS[name]


def checked_admin_url(name, raw_url, fly_app):
    """Return the administrator URL only if app, host, database and user all match."""
    spec = target(name)
    if fly_app != spec['app']:
        raise RuntimeError('Refusing to run this target from this Fly app')
    url = make_url(raw_url.strip().replace('postgres://', 'postgresql://', 1))
    if url.host not in spec['hosts'] or url.database != spec['database'] or url.username != spec['admin_user']:
        raise RuntimeError('Refusing unexpected operator connection')
    return url
