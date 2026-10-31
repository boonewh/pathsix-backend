"""Fetch and validate OAuth Client ID Metadata Documents (CIMD) with SSRF limits.

A CIMD client_id is an HTTPS URL that serves the client's own metadata. Fetching
it means the server makes a request chosen by an untrusted caller, so the fetch
resolves the host once, refuses any non-public address, pins the connection to
that address (TLS still verifies the hostname), follows no redirects and reads
a small bounded body. Network or format failures never reveal response details.
"""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

from app.services.oauth import valid_redirect

MAX_BYTES = 5120
TIMEOUT_SECONDS = 5


class ClientMetadataError(ValueError):
    pass


def is_metadata_client_id(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 200
            or any(ord(c) < 33 or c == '\\' for c in value)):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(parsed.scheme == 'https' and parsed.hostname and port in (None, 443)
                and parsed.path not in ('', '/') and not parsed.query and not parsed.fragment
                and not parsed.username and not parsed.password
                and not any(part in ('.', '..') for part in parsed.path.split('/')))


def public_address(host, resolve=socket.getaddrinfo):
    try:
        ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        pass
    else:
        raise ClientMetadataError('IP literal client identifiers are not accepted')
    try:
        addresses = {info[4][0] for info in resolve(host, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise ClientMetadataError('Client host unavailable') from None
    if not addresses or any(not ipaddress.ip_address(a.split('%')[0]).is_global for a in addresses):
        raise ClientMetadataError('Client host must resolve only to public addresses')
    return sorted(addresses)[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, 443, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch(client_id, resolve=socket.getaddrinfo):
    if not is_metadata_client_id(client_id):
        raise ClientMetadataError('Invalid client identifier')
    parsed = urlsplit(client_id)
    connection = PinnedHTTPSConnection(parsed.hostname, public_address(parsed.hostname, resolve))
    try:
        connection.request('GET', parsed.path, headers={'Accept': 'application/json',
                                                        'User-Agent': 'PathSix-CRM-OAuth/1'})
        response = connection.getresponse()
        media = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
        if response.status != 200 or media != 'application/json':
            raise ClientMetadataError('Client metadata unavailable')
        body = response.read(MAX_BYTES + 1)
    except (OSError, http.client.HTTPException):
        raise ClientMetadataError('Client metadata unavailable') from None
    finally:
        connection.close()
    if len(body) > MAX_BYTES:
        raise ClientMetadataError('Client metadata too large')
    return parse(client_id, body)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def display_name(value, fallback):
    if value is None:
        return fallback
    if (not isinstance(value, str) or not value.strip() or len(value.strip()) > 100
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ClientMetadataError('Invalid client name')
    return value.strip()


def redirect_list(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 10
            or not all(isinstance(uri, str) and valid_redirect(uri) for uri in value)
            or len(set(value)) != len(value)):
        raise ClientMetadataError('Invalid redirect URIs')
    return value


def check_public_code_client(data):
    """Only public authorization-code clients with PKCE are supported."""
    if data.get('token_endpoint_auth_method', 'none') != 'none':
        raise ClientMetadataError('Only public clients are supported')
    grants = data.get('grant_types', ['authorization_code'])
    responses = data.get('response_types', ['code'])
    if (not isinstance(grants, list) or 'authorization_code' not in grants
            or not isinstance(responses, list) or 'code' not in responses):
        raise ClientMetadataError('Authorization code flow required')


def parse(client_id, body):
    try:
        data = json.loads(body, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ClientMetadataError('Invalid client metadata') from None
    if not isinstance(data, dict) or data.get('client_id') != client_id:
        raise ClientMetadataError('Client metadata does not match its identifier')
    check_public_code_client(data)
    return {'name': display_name(data.get('client_name'), urlsplit(client_id).hostname),
            'redirect_uris': redirect_list(data.get('redirect_uris'))}
