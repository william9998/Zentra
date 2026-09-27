from celery import Celery

from apps.api.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "zentra",
    broker=_settings.redis_url,     # ADR-0005：broker 與 result 都是 Redis，不用 RabbitMQ
    backend=_settings.redis_url,
    include=["apps.worker.tasks.run", "apps.worker.tasks.build", "apps.worker.tasks.container"],
)

celery_app.conf.update(
    task_acks_late=True,            # 沙盒跑到一半 worker 掛掉 → 任務重回佇列
    worker_prefetch_multiplier=1,   # 長任務不要預取
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_default_queue="q.run",
    task_routes={
        "zentra.run.execute": {"queue": "q.run"},
        "zentra.build.execute": {"queue": "q.build"},
        # 容器生命週期走 q.build：它跟發布一樣會拉 image、會慢，
        # 塞進 q.run 會卡住真正的 Run 派工。
        "zentra.container.apply": {"queue": "q.build"},
    },
    timezone="UTC",
    enable_utc=True,
)
