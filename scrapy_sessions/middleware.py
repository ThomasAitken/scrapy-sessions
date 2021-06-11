import logging
from collections import defaultdict

from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import Response
from scrapy.utils.python import to_unicode

from scrapy_sessions.objects import DynamicJar, Sessions, Profiles


logger = logging.getLogger(__name__)


class CookiesMiddleware:
    """This middleware enables working with sites that need cookies"""

    def __init__(self, debug=False, profiles=None):
        self.jars = defaultdict(DynamicJar)
        self.debug = debug
        self.profiles = profiles

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool('COOKIES_ENABLED'):
            raise NotConfigured
        profiles = None
        if crawler.settings.getbool('SESSIONS_PROFILES_SYNC'):
            try:
                profiles = crawler.settings.getlist('SESSIONS_PROFILES')
                validate_profiles(profiles)
                profiles = Profiles(profiles)
            except AssertionError:
                raise Exception('Invalid configuration of profiles')

        o = cls(crawler.settings.getbool('COOKIES_DEBUG'), profiles)
        crawler.signals.connect(o.spider_opened, signal=signals.spider_opened)
        return o

    def spider_opened(self, spider):
        spider.sessions = Sessions(self.jars, self.profiles, spider, spider.crawler.engine)

    def process_request(self, request, spider):
        if request.meta.get('dont_merge_cookies', False):
            return

        session_id = request.meta.get('session_id', request.meta.get('cookiejar', 0))
        if session_id == 0:
            # setting session_id here so it can be accessed in Response.meta
            request.meta['session_id'] = session_id
        if self.profiles is not None:
            if session_id not in self.profiles.ref:
                self.profiles.new_session(session_id)

        jar = self.jars[session_id]
        request.meta['_times_jar_renewed'] = jar.times_renewed
        # request is using cleared session but is not the renewal request
        if jar.needs_renewal and jar.has_specified_req and '_renewal' not in request.meta:
            request.dont_filter = True
            spider.crawler.stats.inc_value('retry/count')
            reason = 'old session request'
            spider.crawler.stats.inc_value(f'retry/reason_count/{reason}')
            return request

        for cookie in self._get_request_cookies(jar, request):
            jar.set_cookie_if_ok(cookie, request)

        # set Cookie header
        request.headers.pop('Cookie', None)
        jar.add_cookie_header(request)
        if self.profiles is not None:
            self.profiles.add_profile(session_id, request)
        self._debug_cookie(request, spider)

    def process_response(self, request, response, spider):
        if request.meta.get('dont_merge_cookies', False):
            return response

        session_id = request.meta.get('session_id', request.meta.get('cookiejar', 0))
        jar = self.jars[session_id]
        # response downloaded using session that was cleared
        if jar.times_renewed > request.meta['_times_jar_renewed']:
            request.meta['_times_jar_renewed'] = jar.times_renewed
            request.dont_filter = True
            spider.crawler.stats.inc_value('retry/count')
            reason = 'old session request'
            spider.crawler.stats.inc_value(f'retry/reason_count/{reason}')
            return request

        # convenient attribute
        request.meta["cookies"] = get_neat_cookies(response.headers)

        # extract cookies from Set-Cookie and drop invalid/expired cookies
        jar.extract_cookies(response, request)
        self._debug_set_cookie(response, spider)
        if jar.needs_renewal:
            jar.needs_renewal = False
            jar.has_specified_req = False
            jar.times_renewed += 1
            spider.logger.info('Session %d renewed with request to %s' % (session_id, request.url))
            spider.crawler.stats.inc_value('sesssions/renewal_events')

        return response

    def _debug_cookie(self, request, spider):
        if self.debug:
            cl = [to_unicode(c, errors='replace')
                  for c in request.headers.getlist('Cookie')]
            if cl:
                cookies = "\n".join(f"Cookie: {c}\n" for c in cl)
                msg = f"Sending cookies to: {request}\n{cookies}"
                logger.debug(msg, extra={'spider': spider})

    def _debug_set_cookie(self, response, spider):
        if self.debug:
            cl = [to_unicode(c, errors='replace')
                  for c in response.headers.getlist('Set-Cookie')]
            if cl:
                cookies = "\n".join(f"Set-Cookie: {c}\n" for c in cl)
                msg = f"Received cookies from: {response}\n{cookies}"
                logger.debug(msg, extra={'spider': spider})

    def _format_cookie(self, cookie, request):
        """
        Given a dict consisting of cookie components, return its string representation.
        Decode from bytes if necessary.
        """
        decoded = {}
        for key in ("name", "value", "path", "domain"):
            if cookie.get(key) is None:
                if key in ("name", "value"):
                    msg = "Invalid cookie found in request {}: {} ('{}' is missing)"
                    logger.warning(msg.format(request, cookie, key))
                    return
                continue
            if isinstance(cookie[key], str):
                decoded[key] = cookie[key]
            else:
                try:
                    decoded[key] = cookie[key].decode("utf8")
                except UnicodeDecodeError:
                    logger.warning("Non UTF-8 encoded cookie found in request %s: %s",
                                   request, cookie)
                    decoded[key] = cookie[key].decode("latin1", errors="replace")

        cookie_str = f"{decoded.pop('name')}={decoded.pop('value')}"
        for key, value in decoded.items():  # path, domain
            cookie_str += f"; {key.capitalize()}={value}"
        return cookie_str

    def _get_request_cookies(self, jar, request):
        """
        Extract cookies from the Request.cookies attribute
        """
        if not request.cookies:
            return []
        elif isinstance(request.cookies, dict):
            cookies = ({"name": k, "value": v} for k, v in request.cookies.items())
        else:
            cookies = request.cookies
        formatted = filter(None, (self._format_cookie(c, request) for c in cookies))
        response = Response(request.url, headers={"Set-Cookie": formatted})
        return jar.make_cookies(response, request)


def validate_profiles(profiles):
    for p in profiles:
        assert(isinstance(p, dict))
        assert('proxy' in p or 'user-agent' in p)
        if 'proxy' in p:
            assert(len(p['proxy']) == 2)
        if 'user-agent' in p:
            assert(isinstance(p['user-agent'], str))


def get_neat_cookies(resp_headers):
    """Returns list of cookies received from last request.
    """
    cl = [to_unicode(c, errors='replace')
            for c in resp_headers.getlist('Set-Cookie')]
    cl_fancy = []
    for c in cl:
        content = c.split('; ')[0]
        split = content.split('=')
        key = split[0]
        val = ''.join(split[1:])
        cl_fancy.append({key: val})
    return cl_fancy