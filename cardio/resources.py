"""One bounded FIFO gate for model requests made by this workstation process.

No prompts, patient text, or invented device telemetry are retained here.
"""
import threading
import time
import uuid
from contextlib import contextmanager
from collections import deque

from werkzeug.exceptions import ServiceUnavailable


class ModelGate:
    def __init__(self, capacity=8):
        self.condition = threading.Condition()
        self.queue = deque()
        self.capacity = capacity
        self.active = None
        self.completed = self.failed = self.rejected = 0
        self.history = deque(maxlen=50)

    def snapshot(self):
        with self.condition:
            return {'scope': 'this_workstation_process', 'active': self.active,
                    'waiting': len(self.queue), 'completed': self.completed, 'failed': self.failed, 'rejected': self.rejected,
                    'recent': list(self.history), 'device_execution_verified': False}

    @contextmanager
    def slot(self, endpoint, timeout=120):
        ident, begin = uuid.uuid4().hex, time.perf_counter()
        with self.condition:
            if len(self.queue) >= self.capacity:
                self.rejected += 1
                raise ServiceUnavailable('本機模型佇列已滿，請稍後重試。')
            self.queue.append(ident)
            while self.active is not None or self.queue[0] != ident:
                remaining = timeout - (time.perf_counter() - begin)
                if remaining <= 0:
                    self.rejected += 1
                    self.queue.remove(ident)
                    self.condition.notify_all()
                    raise ServiceUnavailable('等候本機模型逾時，尚未發送模型請求。')
                self.condition.wait(remaining)
            self.queue.popleft()
            self.active = {'request_id': ident, 'endpoint': endpoint}
        started = time.perf_counter()
        record = {'request_id': ident, 'endpoint': endpoint,
                  'queue_ms': round((started - begin) * 1000, 3),
                  'device_execution_verified': False}
        success = False
        try:
            yield record
            success = True
        finally:
            record['execution_ms'] = round((time.perf_counter() - started) * 1000, 3)
            record['success'] = success
            with self.condition:
                self.completed += int(success)
                self.failed += int(not success)
                self.history.append(record.copy())
                self.active = None
                self.condition.notify_all()
