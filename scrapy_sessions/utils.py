import os
from importlib import import_module, util
from scrapy.utils.conf import closest_scrapy_cfg

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