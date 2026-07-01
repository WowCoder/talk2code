# -*- coding: utf-8 -*-
"""
SSE 连接管理器
提供线程安全的 SSE 客户端管理，支持心跳检测、超时清理、断线重连、消息缓冲回放
"""

import threading
import queue
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional
from harness.observability.logger import get_logger

logger = get_logger(__name__)

# 每个 client_id 最多缓冲的消息数
MAX_BUFFERED_MESSAGES = 200


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
        """检查连接是否仍然活跃"""
        return (datetime.now() - self.last_heartbeat).seconds < timeout_seconds

    def send(self, message: str) -> bool:
        """发送消息到客户端"""
        try:
            self.queue.put_nowait(message)
            self.message_count += 1
            return True
        except queue.Full:
            logger.warning(f"SSE 队列已满，丢弃消息")
            return False


class SSEManager:
    """SSE 连接管理器（线程安全，支持消息缓冲回放）"""

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

        # 启动后台清理线程
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="SSE-Cleanup"
        )
        self._cleanup_thread.start()

        logger.info("SSEManager 已初始化（含消息缓冲回放）")

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
        """向指定 client_id 的所有客户端广播消息，同时写入缓冲"""
        with self._lock:
            # 写入消息缓冲（用于迟到客户端回放）
            if client_id not in self._message_buffers:
                self._message_buffers[client_id] = deque(maxlen=MAX_BUFFERED_MESSAGES)
            self._message_buffers[client_id].append(message)

            # 发送给当前连接的客户端
            if client_id not in self._clients:
                return 0

            sent_count = 0
            for client in self._clients[client_id]:
                if client.send(message):
                    sent_count += 1

            return sent_count

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
            threading.Event().wait(60)  # 每分钟清理一次

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

            # 清理无客户端且空闲超过 10 分钟的缓冲
            idle_buffer_ids = []
            for cid in list(self._message_buffers.keys()):
                if cid not in self._clients:
                    idle_buffer_ids.append(cid)

            for cid in idle_buffer_ids:
                del self._message_buffers[cid]
                logger.debug(f"清理孤儿消息缓冲：client_id={cid}")

        if cleaned:
            logger.info(f"清理了 {len(cleaned)} 个超时 SSE 连接")
            for info in cleaned[:5]:  # 只显示前 5 个
                logger.debug(f"  - {info}")

    def shutdown(self):
        """关闭管理器，停止后台线程"""
        self._running = False
        with self._lock:
            self._clients.clear()
            self._message_buffers.clear()
        logger.info("SSEManager 已关闭")


# 全局单例
sse_manager = SSEManager()
