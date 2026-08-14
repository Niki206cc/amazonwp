"""Runtime URL normalization for urllib.

Python imports sitecustomize automatically at startup when this project directory is
on sys.path. Amazon search/product URLs sometimes contain non-ASCII characters
(e.g. the __mk_it_IT value). urllib/http.client expects the request target to be
ASCII, so encode only the URL components that need percent-encoding while
preserving existing escapes and URL separators.
"""

import urllib.parse
import urllib.request


_original_request_init = urllib.request.Request.__init__


def _ascii_safe_url(url):
    if not isinstance(url, str):
        return url

    url = url.strip().strip("'\"")
    if url.startswith("//"):
        url = "https:" + url
    elif "://" not in url and url.lower().startswith(("amazon.it/", "www.amazon.it/")):
        url = "https://" + url

    try:
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~%")
        query = urllib.parse.quote(parts.query, safe="=&;%:+,/?@!$'()*-._~")
        fragment = urllib.parse.quote(parts.fragment, safe="=&;%:+,/?@!$'()*-._~")
        hostname = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
        netloc = hostname
        if parts.port:
            netloc += f":{parts.port}"
        if parts.username:
            userinfo = urllib.parse.quote(parts.username, safe="-._~%")
            if parts.password:
                userinfo += ":" + urllib.parse.quote(parts.password, safe="-._~%")
            netloc = userinfo + "@" + netloc
        return urllib.parse.urlunsplit((parts.scheme, netloc or parts.netloc, path, query, fragment))
    except Exception:
        return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-._~%")


def _request_init(self, url, *args, **kwargs):
    return _original_request_init(self, _ascii_safe_url(url), *args, **kwargs)


urllib.request.Request.__init__ = _request_init
