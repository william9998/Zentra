"""Celery 生產端。

**刻意只用 send_task(字串任務名)**，這樣 `api` 永遠不 import `apps.worker.tasks.*`——
那條 import 會把 docker SDK 與沙盒 driver 一起拉進 api 行程，而 api 不該碰那些東西。
"""

from celery import Celery

from apps.api.config import get_settings

_settings = get_settings()

RUN_TASK_NAME = "zentra.run.execute"
RUN_QUEUE = "q.run"

_producer = Celery("zentra-producer", broker=_settings.redis_url, backend=_settings.redis_url)
_producer.conf.task_routes = {RUN_TASK_NAME: {"queue": RUN_QUEUE}}


def dispatch_run(run_id: str) -> str:
    result = _producer.send_task(RUN_TASK_NAME, args=[run_id], task_id=run_id)
    return result.id
