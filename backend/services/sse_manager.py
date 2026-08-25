# -*- coding: utf-8 -*-
"""
SSE 连接管理器（Redis Pub/Sub 增强）

架构:
  - 本地: 维护 client_id -> List[SSEClient] 映射,每个客户端一个 queue.Queue
  - 跨进程: broadcast() 同时 publish 到 Redis channel,后台线程 subscribe 并转发给本地客户端
  - 降级: Redis 不可用时回退到纯本地广播

多 worker 场景:
  Worker A (Celery/Flask) → Redis Pub → Worker B (Flask SSE) → 浏览器
  Worker A (Celery/Flask) → Redis Pub → Worker A (Flask SSE) → 浏览器
"""

import threading
import queue
import json
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)

# 每个 client_id 最多缓冲的消息数
MAX_BUFFERED_MESSAGES = 200

# Redis Pub/Sub channel 前缀
SSE_CHANNEL_PREFIX = "sse:"


class SSEClient:
    """表示一个 SSE 客户端连接"""

    def __init__(self, client_queue: queue.Queue):
        self.queue = client_queue
        self.connected_at = datetime.now()
        self.last_heartbeat = datetime.now()
        self.message_count = 0

    def update_heartbeat(self):
        """更新心跳时间"""
        self.last_heartbeat = datetime.now()

    def is_alive(self, timeout_seconds: int = 300) -> bool:
        """检查连接是否仍然活跃（total_seconds，避免跨天 .seconds 回绕）"""
        return (datetime.now() - self.last_heartbeat).total_seconds() < timeout_seconds

    def send(self, message: str) -> bool:
        """发送消息到客户端（发送即视为活跃，同步更新心跳）"""
        try:
            self.queue.put_nowait(message)
            self.message_count += 1
            self.update_heartbeat()
            return True
        except queue.Full:
            logger.warning(f"SSE 队列已满，丢弃消息")
            return False


class SSEManager:
    """SSE 连接管理器（线程安全，支持 Redis Pub/Sub 跨进程分发）

    降级策略:
    - Redis 可用: broadcast() 同时发本地 + Redis publish,后台线程订阅并转发
    - Redis 不可用: 自动降级为纯本地广播（和原版一致）
    """

    _instance: Optional['SSEManager'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'SSEManager':
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # client_id -> List[SSEClient]
        self._clients: Dict[str, List[SSEClient]] = {}
        self._lock = threading.RLock()  # 可重入锁，支持嵌套调用

        # 消息缓冲：client_id -> deque of messages（用于回放给迟到客户端）
        self._message_buffers: Dict[str, deque] = {}

        # Redis Pub/Sub（延迟初始化）
        self._redis = None
        self._redis_available = False
        self._redis_thread: Optional[threading.Thread] = None

        # 消息缓冲最后访问时间（用于清理长时间无客户端且空闲的缓冲）
        self._buffer_ts: Dict[str, float] = {}

        # 运行标志（必须在启动后台线程前设置，避免 _redis_subscribe_loop 提前访问）
        self._running = True

        # 尝试连接 Redis（启动后台订阅线程）
        self._init_redis()

        # 启动后台清理线程
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="SSE-Cleanup"
        )
        self._cleanup_thread.start()

        if self._redis_available:
            logger.info("SSEManager 已初始化（Redis Pub/Sub 跨进程分发已启用）")
        else:
            logger.info("SSEManager 已初始化（Redis 不可用，降级为本地广播）")

    def _init_redis(self):
        """初始化 Redis 连接和 Pub/Sub 订阅"""
        try:
            import redis as redis_lib
            from config import settings
            self._redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis.ping()
            self._redis_available = True

            # 启动后台订阅线程
            self._redis_thread = threading.Thread(
                target=self._redis_subscribe_loop,
                daemon=True,
                name="SSE-Redis-Sub"
            )
            self._redis_thread.start()
        except Exception as e:
            logger.warning(f"Redis 连接失败，SSE 降级为本地广播: {e}")
            self._redis_available = False

    def _redis_subscribe_loop(self):
        """Redis Pub/Sub 订阅循环: 监听 sse:* channel,转发消息给本地客户端"""
        import redis as redis_lib
        from config import settings

        while self._running:
            conn = None
            pubsub = None
            try:
                # 每次连接创建新的 pubsub 对象（避免重连问题）
                conn = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
                pubsub = conn.pubsub()
                # 使用 psubscribe 订阅所有 sse:* channel
                pubsub.psubscribe(f"{SSE_CHANNEL_PREFIX}*")
                logger.info("SSE Redis 订阅已启动，监听 channel: %s*" % SSE_CHANNEL_PREFIX)

                for message in pubsub.listen():
                    if not self._running:
                        break
                    if message['type'] != 'pmessage':
                        continue

                    # channel 格式: sse:{client_id}
                    channel = message['channel']
                    if not channel.startswith(SSE_CHANNEL_PREFIX):
                        continue

                    client_id = channel[len(SSE_CHANNEL_PREFIX):]
                    data = message['data']

                    # 转发给本地客户端（不写入缓冲,因为 publish 的 worker 已经写过缓冲）
                    self._local_broadcast(client_id, data)

            except Exception as e:
                if self._running:
                    logger.error(f"SSE Redis 订阅异常，5 秒后重连: {e}")
                    import time
                    time.sleep(5)
            finally:
                # 无论正常退出还是异常，都要释放 conn/pubsub，避免连接泄漏
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _local_broadcast(self, client_id: str, message: str) -> int:
        """本地广播: 发送给当前进程内的客户端（不写缓冲）"""
        with self._lock:
            if client_id not in self._clients:
                return 0

            sent_count = 0
            for client in self._clients[client_id]:
                if client.send(message):
                    sent_count += 1

            return sent_count

    def add_client(self, client_id: str, client_queue: queue.Queue) -> SSEClient:
        """添加新的 SSE 客户端，并回放缓冲的消息"""
        with self._lock:
            if client_id not in self._clients:
                self._clients[client_id] = []

            client = SSEClient(client_queue)
            self._clients[client_id].append(client)
            logger.debug(f"添加 SSE 客户端：client_id={client_id}, 当前连接数={len(self._clients[client_id])}")

            # 回放缓冲的消息给新客户端（避免因连接延迟丢失早期事件）
            buffer = self._message_buffers.get(client_id)
            if buffer:
                replayed = 0
                for msg in buffer:
                    if client.send(msg):
                        replayed += 1
                if replayed > 0:
                    logger.info(f"回放 {replayed} 条缓冲消息给 client_id={client_id}")

            return client

    def remove_client(self, client_id: str, client_queue: queue.Queue) -> bool:
        """移除 SSE 客户端"""
        with self._lock:
            if client_id not in self._clients:
                return False

            original_count = len(self._clients[client_id])
            self._clients[client_id] = [
                c for c in self._clients[client_id]
                if c.queue is not client_queue
            ]

            # 如果没有客户端了，删除整个 entry
            if not self._clients[client_id]:
                del self._clients[client_id]
                # 保留缓冲一段时间（等可能的重连），由 cleanup 负责清理

            removed = original_count - len(self._clients.get(client_id, []))
            if removed > 0:
                logger.debug(f"移除 SSE 客户端：client_id={client_id}, 移除={removed}")
            return removed > 0

    def broadcast(self, client_id: str, message: str) -> int:
        """向指定 client_id 的所有客户端广播消息

        流程:
        1. 写入本地消息缓冲（用于迟到客户端回放）
        2. 本地广播给当前进程的客户端
        3. publish 到 Redis（其他 worker 的客户端会收到）
        """
        with self._lock:
            # 1. 写入消息缓冲（用于迟到客户端回放），并记录最后访问时间
            if client_id not in self._message_buffers:
                self._message_buffers[client_id] = deque(maxlen=MAX_BUFFERED_MESSAGES)
            self._message_buffers[client_id].append(message)
            import time as _t
            self._buffer_ts[client_id] = _t.time()

        # 2. 本地广播
        local_count = self._local_broadcast(client_id, message)

        # 3. Redis publish（跨进程分发）
        if self._redis_available:
            try:
                channel = f"{SSE_CHANNEL_PREFIX}{client_id}"
                self._redis.publish(channel, message)
            except Exception as e:
                logger.warning(f"Redis publish 失败（降级为本地）: {e}")

        return local_count

    def get_client_count(self, client_id: str) -> int:
        """获取指定 client_id 的客户端数量"""
        with self._lock:
            return len(self._clients.get(client_id, []))

    def get_total_clients(self) -> int:
        """获取总客户端数量"""
        with self._lock:
            return sum(len(clients) for clients in self._clients.values())

    def _cleanup_loop(self):
        """后台清理线程：定期清理超时连接和孤儿缓冲"""
        while self._running:
            try:
                self.cleanup_stale()
            except Exception as e:
                logger.error(f"SSE 清理线程异常：{e}")
            import time
            time.sleep(60)  # 每分钟清理一次

    def cleanup_stale(self, timeout_seconds: int = 300):
        """清理超时连接和孤儿缓冲"""
        now = datetime.now()
        cleaned = []

        with self._lock:
            for client_id in list(self._clients.keys()):
                active_clients = []
                for client in self._clients[client_id]:
                    if client.is_alive(timeout_seconds):
                        active_clients.append(client)
                    else:
                        cleaned.append({
                            'client_id': client_id,
                            'age': (now - client.connected_at).seconds,
                            'messages_sent': client.message_count
                        })

                if active_clients:
                    self._clients[client_id] = active_clients
                else:
                    del self._clients[client_id]

            # 清理无客户端且空闲超过 10 分钟的缓冲（给重连窗口留时间，
            # 与 remove_client 中"保留缓冲一段时间"的注释意图一致）
            import time as _t
            idle_buffer_ids = []
            for cid in list(self._message_buffers.keys()):
                last_ts = self._buffer_ts.get(cid, 0)
                if cid not in self._clients and (_t.time() - last_ts) > 600:
                    idle_buffer_ids.append(cid)

            for cid in idle_buffer_ids:
                del self._message_buffers[cid]
                self._buffer_ts.pop(cid, None)
                logger.debug(f"清理孤儿消息缓冲：client_id={cid}")

        if cleaned:
            logger.info(f"清理了 {len(cleaned)} 个超时 SSE 连接")
            for info in cleaned[:5]:  # 只显示前 5 个
                logger.debug(f"  - {info}")

    def shutdown(self):
        """关闭管理器，停止后台线程并释放连接"""
        self._running = False
        with self._lock:
            self._clients.clear()
            self._message_buffers.clear()
            self._buffer_ts.clear()

        # 关闭 Redis 连接（订阅线程持有的 conn/pubsub 由线程内 finally 负责释放）
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None
        self._redis_available = False

        logger.info("SSEManager 已关闭")


# 全局单例
sse_manager = SSEManager()
