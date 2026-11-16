"""Keep personal and CRM data out of Sentry reports.

Sentry's defaults attach local variable values to every stack frame, which can
include typed passwords, bearer tokens and record contents. Reports keep the
error type and code location, which is what diagnosing a failure needs.
"""
from urllib.parse import urlsplit

# The AI connector and sign-in paths handle tokens, consent and CRM fields that
# an AI host sent, so their exception messages are dropped as well.
PRIVATE_PATH_PREFIXES = ('/mcp', '/oauth', '/connect', '/api/login',
                         '/api/forgot-password', '/api/reset-password', '/api/change-password')

SENTRY_PRIVACY_OPTIONS = {
    'send_default_pii': False,
    'include_local_variables': False,
    'max_request_body_size': 'never',
}


def _is_private(event):
    url = (event.get('request') or {}).get('url') or ''
    path = urlsplit(url).path or event.get('transaction') or ''
    return path.startswith(PRIVATE_PATH_PREFIXES)


def scrub_event(event, hint=None):
    request = event.get('request')
    if isinstance(request, dict):
        for key in ('query_string', 'cookies', 'data', 'env'):
            request.pop(key, None)
        if isinstance(request.get('url'), str):
            request['url'] = request['url'].split('?', 1)[0]
        headers = request.get('headers')
        if isinstance(headers, dict):
            request['headers'] = {k: v for k, v in headers.items()
                                  if k.lower() in ('content-type', 'content-length', 'user-agent', 'host')}
    user = event.get('user')
    if isinstance(user, dict):
        event['user'] = {k: v for k, v in user.items() if k == 'id'}
    private = _is_private(event)
    for exception in ((event.get('exception') or {}).get('values') or []):
        if private:
            exception['value'] = ''
        for frame in ((exception.get('stacktrace') or {}).get('frames') or []):
            frame.pop('vars', None)
    if private:
        event.pop('breadcrumbs', None)
        if isinstance(event.get('logentry'), dict):
            event['logentry'].pop('params', None)
    return event
