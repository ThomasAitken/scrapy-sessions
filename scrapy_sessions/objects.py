import itertools
import logging
from http.cookiejar import time2netscape
from scrapy.http.cookies import CookieJar
from scrapy.utils.log import failure_to_exc_info


logger = logging.getLogger(__name__)


class DynamicJar(CookieJar):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.needs_renewal = False
        self.times_renewed = 0


class Sessions:
    logger = logging.getLogger(__name__)

    def __init__(self, jars, profiles, spider, engine):
        self.jars=jars
        self.profiles=profiles
        self.spider=spider
        self.engine=engine

    def __repr__(self):
        if not x:
            return '{}'
        out = {}
        for k in self:
            out[k] = self.get(k)
        return str(out)

    @staticmethod
    def _flatten_cookiejar(jar):
        """Returns map object of cookies in http.Cookiejar.Cookies format
        """
        full_cookies = list(itertools.chain.from_iterable(
                            p.values() for p in jar._cookies.values()))[0]
        cookies = full_cookies.values()
        return cookies

    @staticmethod
    def _httpcookie_to_dict(cookie):
        simple_cookie = {getattr(cookie, 'name'): getattr(cookie, 'value')}
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
    
    def get(self, session_id=0, mode=None):
        """Returns list of cookies for the given session.
        For inspection not editing.
        """
        jar = self._get(session_id)
        if not jar._cookies:
            return {}
        cookies = self._flatten_cookiejar(jar)
        neat_cookies = []
        for c in cookies:
            if mode == dict:
                neat_cookies.append(self._httpcookie_to_dict(c))
            else:
                neat_cookies.append(self._httpcookie_to_str(c))

        return neat_cookies

    def get_profile(self, session_id=0):
        if self.profiles is not None:
            return self.profiles.ref.get(session_id, None)
        raise Exception('Can\'t use get_profile function when SESSIONS_PROFILES_SYNC is not enabled')

    def clear(self, session_id=0, renewal_request=None):
        jar = self._get(session_id)
        jar.needs_renewal = True
        jar.clear()
        if self.profiles is not None:
            self.profiles._clear(session_id)

        if renewal_request is not None:
            if renewal_request.callback is None:
                renewal_request.callback=self._renew
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
