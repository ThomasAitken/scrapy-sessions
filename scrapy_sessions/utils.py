import os
import logging
from importlib import import_module, util
from scrapy.utils.conf import closest_scrapy_cfg

logger = logging.getLogger(__name__)

def load_profiles(path):
    def get_project_dir():
        closest_cfg = closest_scrapy_cfg()
        if closest_cfg:
            outer_dir = os.path.dirname(closest_cfg)
        if outer_dir:
            return outer_dir
        scrapy_module = os.environ.get('SCRAPY_SETTINGS_MODULE')
        if scrapy_module is None and not outer_dir:
            raise Exception("Project configuration awry")
        module = import_module(scrapy_module)
        outer_dir = os.path.dirname(os.path.dirname(module.__file__))
        return outer_dir
    input_path = os.path.join(get_project_dir(), path)
    spec = util.spec_from_file_location('profiles', input_path)
    profiles = util.module_from_spec(spec)
    spec.loader.exec_module(profiles)
    return profiles.PROFILES

def format_cookie(cookie, request):
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