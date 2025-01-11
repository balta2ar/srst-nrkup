from os import environ
from os.path import expanduser, expandvars

DEFUALT_WIDTH = 100


def must_env(name):
    value = expandvars(expanduser(environ.get(name, '')))
    if not value:
        raise RuntimeError('env var "{0}" must be set'.format(name))
    return value
