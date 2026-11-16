"""Sentry reports keep error type and location, never personal or CRM data."""
import sentry_sdk
from app.utils.sentry_privacy import SENTRY_PRIVACY_OPTIONS, scrub_event


def report(url, message='Lead "Acme Roofing" for private@example.test'):
    return {'request': {'url': url + '?code=secret-code', 'query_string': 'code=secret-code',
                        'cookies': {'session': 'x'}, 'data': {'password': 'hunter2'},
                        'headers': {'Authorization': 'Bearer token', 'Cookie': 'a=b', 'Content-Type': 'application/json'}},
            'user': {'id': 7, 'email': 'private@example.test', 'ip_address': '203.0.113.5'},
            'breadcrumbs': {'values': [{'message': 'private'}]},
            'exception': {'values': [{'type': 'ValueError', 'value': message,
                'stacktrace': {'frames': [{'function': 'login', 'lineno': 10, 'vars': {'password': 'hunter2'}}]}}]}}


def test_every_report_drops_request_data_locals_and_identity():
    event = scrub_event(report('https://crm.example.test/api/leads'))
    # An ordinary CRM page keeps the message for debugging; everything else goes.
    text = repr({k: v for k, v in event.items() if k != 'exception'}) + repr(event['exception']['values'][0]['stacktrace'])
    for private in ('secret-code', 'hunter2', 'Bearer token', 'a=b', 'private@example.test', '203.0.113.5'):
        assert private not in text
    assert event['request']['url'] == 'https://crm.example.test/api/leads'
    assert event['request']['headers'] == {'Content-Type': 'application/json'}
    assert event['user'] == {'id': 7}
    frame = event['exception']['values'][0]['stacktrace']['frames'][0]
    assert frame == {'function': 'login', 'lineno': 10}


def test_ai_connector_and_sign_in_reports_also_drop_messages():
    for path in ('/mcp', '/oauth/token', '/oauth/actions/x', '/api/login', '/api/forgot-password'):
        event = scrub_event(report('https://api.example.test' + path))
        assert 'Acme' not in repr(event) and 'private@' not in repr(event)
        assert event['exception']['values'][0]['type'] == 'ValueError'  # still diagnosable
        assert 'breadcrumbs' not in event


def test_sentry_is_configured_without_local_variables_or_pii():
    assert SENTRY_PRIVACY_OPTIONS == {'send_default_pii': False, 'include_local_variables': False,
                                      'max_request_body_size': 'never'}
    client = sentry_sdk.Client(dsn=None, **SENTRY_PRIVACY_OPTIONS, before_send=scrub_event)
    assert client.options['include_local_variables'] is False and client.options['before_send'] is scrub_event
