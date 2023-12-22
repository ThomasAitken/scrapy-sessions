import itertools
import logging
from http.cookiejar import time2netscape
from scrapy.http import Request, Response
from scrapy.http.cookies import CookieJar
from scrapy.utils.log import failure_to_exc_info

from scrapy_sessions.utils import format_cookie

logger = logging.getLogger(__name__)


class DynamicJar(CookieJar):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.needs_renewal = False
        self.has_specified_req = False
        self.times_renewed = 0


class Sessions:
    logger = logging.getLogger(__name__)

    def __init__(self, jars, profiles, spider, engine):
        self.jars=jars
        self.profiles=profiles
        self.spider=spider
        self.engine=engine

    def __repr__(self):
        out = ""
        for k in self.jars.keys():
            out += repr(self.get(k)) + "\n\n"
        out = out.rstrip("\n")
        return out

    @staticmethod
    def _flatten_cookiejar(jar):
        """Returns map object of cookies in http.Cookiejar.Cookies format
        """
        cookies = {}
        for domain, val in jar._cookies.items():
            full_cookies = list(val.values())[0]
            cookies[domain] = full_cookies.values()
        return cookies

    @staticmethod
    def _httpcookie_to_tuple(cookie):
        simple_cookie = (getattr(cookie, 'name'), getattr(cookie, 'value'))
        return simple_cookie

    @staticmethod
    def _httpcookie_to_str(cookie):
        content = getattr(cookie, 'name') + '=' + getattr(cookie, 'value')
        expires = 'expires=' + time2netscape(getattr(cookie, 'expires'))
        path = 'path=' + getattr(cookie, 'path')
        domain = 'domain=' + getattr(cookie, 'domain')
        out_str = f'{content}; {expires}; {path}; {domain}'
        return out_str

    def _get(self, session_id=0):
        return self.jars[session_id]
    
    def get(self, session_id=0, mode=None, domain=None):
        """Returns list of cookies for the given session.
        For inspection not editing.
        """
        jar = self._get(session_id)
        if not jar._cookies:
            return {}
        cookies = self._flatten_cookiejar(jar)
        if domain is None:
            # default to first domain. assume that if no domain specified, only one domain of interest
            domain = next(iter(cookies.keys()))
        cookies = cookies[domain]
        if mode == dict:
            neat_cookies = dict(self._httpcookie_to_tuple(c) for c in cookies)
        else:
            neat_cookies = [self._httpcookie_to_str(c) for c in cookies]

        return neat_cookies

    def get_profile(self, session_id=0):
        if self.profiles is not None:
            return self.profiles.ref.get(session_id, None)
        raise Exception('Can\'t use get_profile function when SESSIONS_PROFILES_SYNC is not enabled')
        
    def add_formatted_cookies_manually(self, formatted_cookies, url, session_id=0):
        request = Request(url)
        response = Response(request.url, headers={"Set-Cookie": formatted_cookies})
        jar = self._get(session_id)
        for cookie in jar.make_cookies(response, request):
            jar.set_cookie_if_ok(cookie, request)
            
    def add_cookies_manually(self, cookies, url, session_id=0):
        cookies = ({"name": k, "value": v} for k, v in cookies.items())
        request = Request(url)
        formatted = filter(None, (format_cookie(c, request) for c in cookies))
        response = Response(request.url, headers={"Set-Cookie": formatted})
        jar = self._get(session_id)
        for cookie in jar.make_cookies(response, request):
            jar.set_cookie_if_ok(cookie, request)

    def clear(self, session_id=0, renewal_request=None):
        jar = self._get(session_id)
        jar.needs_renewal = True
        jar.clear()
        if self.profiles is not None:
            self.profiles._clear(session_id)

        if renewal_request is not None:
            jar.has_specified_req = True
            if renewal_request.callback is None:
                renewal_request.callback=self._renew
            renewal_request.meta.update({'_renewal': True})
            renewal_request.dont_filter=True
            self._download_request(renewal_request)

    def _download_request(self, request):
        d = self.engine._download(request, self.spider)
        d.addBoth(self.engine._handle_downloader_output, request, self.spider)
        d.addErrback(lambda f: logger.info('Error while handling downloader output',
                                        exc_info=failure_to_exc_info(f),
                                        extra={'spider': self.spider}))
        d.addBoth(lambda _: self.engine.slot.remove_request(request))
        d.addErrback(lambda f: logger.info('Error while removing request from slot',
                                        exc_info=failure_to_exc_info(f),
                                        extra={'spider': self.spider}))
        d.addBoth(lambda _: self.engine.slot.nextcall.schedule())
        d.addErrback(lambda f: logger.info('Error while scheduling new request',
                                        exc_info=failure_to_exc_info(f),
                                        extra={'spider': self.spider}))

    def _renew(self, response, **cb_kwargs):
        pass


class Profiles(object):
    """Controls profile storage and rotation. Rotation is linear then queue-like once all used"""

    def __init__(self, profiles):
        self.profiles = profiles
        self.available = list(range(len(self.profiles)))
        self.used = []
        # stores keys=session_ids, values=profiles
        self.ref = {}

    def _clear(self, session_id):
        del self.ref[session_id]

    def new_session(self, session_id):
        available = self.get_fresh()
        self.ref[session_id] = self.profiles[available]

    # grabs FIFO from available queue
    def get_fresh(self):
        try:
            out = self.available.pop(0)
        except IndexError:
            # reset
            self.available = self.used
            out = self.available.pop(0)
            self.used = []
        self.used.append(out)
        return out

    def add_profile(self, session_id, request):
        profile = self.ref[session_id]
        if 'proxy' in profile:
            request.meta['proxy'] = profile['proxy'][0]
            request.headers['Proxy-Authorization'] = profile['proxy'][1]
        if 'user-agent' in profile:
            request.headers['User-Agent'] = profile['user-agent']
