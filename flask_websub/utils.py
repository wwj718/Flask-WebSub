from flask import abort, current_app
from celery import Celery
import requests

import contextlib
import hashlib
import hmac
import sqlite3
import uuid

INVALID_LEASE = "Invalid hub.lease_seconds (should be a positive integer)"
RACE_CONDITION = ("Race condition. Subscription '%s' disappeared during this "
                  "request")
A_MINUTE = 60
A_DAY = A_MINUTE * 60 * 24


# Source: http://flask.pocoo.org/docs/0.12/patterns/celery/
def make_celery(app):
    celery = Celery(app.import_name,
                    backend=app.config.get('CELERY_RESULT_BACKEND'),
                    broker=app.config['CELERY_BROKER_URL'],)
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery


def parse_lease_seconds(value):
    try:
        lease_seconds = int(value)
        assert lease_seconds > 0
    except (ValueError, AssertionError):
        abort(400, INVALID_LEASE)
    else:
        return lease_seconds


def uuid4():
    return str(uuid.uuid4())


def request_url(*args, **kwargs):
    # 3 seconds seems reasonable even for slow/far away servers, as websub
    # requests should not do elaborate processing anyway.
    kwargs['timeout'] = current_app.config.get('REQUEST_TIMEOUT', 3)
    return requests.request(*args, **kwargs)


def warn(msg, exc_info):
    current_app.logger.warning(msg, exc_info=exc_info)


def calculate_hmac(algorithm, secret, data):
    hash = getattr(hashlib, algorithm)
    return hmac.new(secret.encode('UTF-8'), data, hash).hexdigest()


def get_content(topic_url):
    updated_content = request_url('GET', topic_url, stream=True)
    updated_content.raise_for_status()
    return updated_content


@contextlib.contextmanager
def logging_race_condition(*key):
    try:
        yield
    except KeyError as e:
        warn(RACE_CONDITION % key, e)


def secret_too_big(secret):
    # 200 bytes actually (not characters), but this is close enough as a
    # sanity check
    return len(secret) >= 200


class SQLite3StorageMixin:
    def __init__(self, path):
        """Path should be where you want to save the sqlite3 database."""

        self.path = path
        self.conn.execute(self.TABLE_SETUP_SQL)

    @property
    def conn(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        # allow writing and reading at the same time:
        connection.execute('PRAGMA journal_mode=wal')
        return connection
