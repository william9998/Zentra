"""Celery 生產端。

**刻意只用 send_task(字串任務名)**，這樣 `api` 永遠不 import `apps.worker.tasks.*`——
那條 import 會把 docker SDK 與沙盒 driver 一起拉進 api 行程，而 api 不該碰那些東西。
"""

from celery import Celery

from apps.api.config import get_settings

_settings = get_settings()

RUN_TASK_NAME = "zentra.run.execute"
RUN_QUEUE = "q.run"
BUILD_TASK_NAME = "zentra.build.execute"
BUILD_QUEUE = "q.build"
# 容器生命週期（ZEN-015）。走 q.build 而不是 q.run —— 它跟發布一樣是慢的、
# 會拉 image 的工作，塞進 q.run 會卡住真正的 Run 派工。
CONTAINER_TASK_NAME = "zentra.container.apply"
# 常駐容器回寫結果後由 worker 裁決終態（ZEN-015）。走 q.run：它很快，
# 而且跟 Run 的生命週期同一件事。
FINALIZE_TASK_NAME = "zentra.run.finalize"

_producer = Celery("zentra-producer", broker=_settings.redis_url, backend=_settings.redis_url)
_producer.conf.task_routes = {
    RUN_TASK_NAME: {"queue": RUN_QUEUE},
    BUILD_TASK_NAME: {"queue": BUILD_QUEUE},
    CONTAINER_TASK_NAME: {"queue": BUILD_QUEUE},
    FINALIZE_TASK_NAME: {"queue": RUN_QUEUE},
}


def dispatch_run(run_id: str) -> str:
    result = _producer.send_task(RUN_TASK_NAME, args=[run_id], task_id=run_id)
    return result.id


def dispatch_build(agent_version_id: str) -> str:
    result = _producer.send_task(
        BUILD_TASK_NAME, args=[agent_version_id], task_id=f"build:{agent_version_id}"
    )
    return result.id


def _dispatch_container(container_uuid: str, action: str) -> str:
    """把容器的期望狀態交給 worker 去實現。

    **api 只寫 DB 與發任務，不碰 Docker** —— docker.sock 仍然只掛在 worker 上。
    task_id 帶上動作，因為同一個容器的「停止」與「重啟」不該互相去重掉。
    """
    result = _producer.send_task(
        CONTAINER_TASK_NAME, args=[container_uuid, action],
        task_id=f"container:{action}:{container_uuid}",
    )
    return result.id


def dispatch_container_start(container_uuid: str) -> str:
    return _dispatch_container(container_uuid, "start")


def dispatch_container_restart(container_uuid: str) -> str:
    return _dispatch_container(container_uuid, "restart")


def dispatch_container_stop(container_uuid: str) -> str:
    return _dispatch_container(container_uuid, "stop")


def dispatch_finalize(run_id: str) -> str:
    """常駐容器回寫結果 → 請 worker 裁決終態。

    task_id 帶前綴避免與 `dispatch_run` 的 task_id（就是 run_id）相撞。
    """
    result = _producer.send_task(
        FINALIZE_TASK_NAME, args=[run_id], task_id=f"finalize:{run_id}"
    )
    return result.id
