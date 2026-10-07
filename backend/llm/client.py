# -*- coding: utf-8 -*-
"""
统一 LLM 客户端模块
支持 OpenAI 兼容接口 和 Anthropic 兼容接口 两种协议
通过 LLM_PROVIDER 配置切换，支持流式输出、会话记忆、自动重试
"""

import os
import json
import time
import threading
from typing import Generator, List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

import requests
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, settings

# 注意：llm.client 是底层模块，不能在模块顶层 import harness（harness 依赖 llm.client，
# 会形成循环导入）。日志用标准 logging，harness 的日志系统会在应用启动时统一配置 root logger。
import logging as _logging
import logging.handlers as _logging_handlers
logger = _logging.getLogger(__name__)

# LLM 请求/响应专用日志（独立于应用日志，便于排查问题）
import logging as _logging
_llm_logger = _logging.getLogger("llm.traffic")
_llm_logger.setLevel(_logging.DEBUG)
# LLM_TRAFFIC_LOG=0 可整体关闭这份明文流量日志（传输层明文，含完整 prompt）。
# 默认开启：它是 KV-cache 命中率等离线指标（harness/observability/metrics_report.py）
# 唯一的数据源，关掉之后那部分指标就没有数据了 —— 所以只提供开关，不改默认。
if not _llm_logger.handlers and os.environ.get("LLM_TRAFFIC_LOG", "1") != "0":
    import os as _os
    # 锚定 backend/logs —— 与 setup_logging 的 BACKEND_DIR / LOG_DIR 同一目录。
    # 此前是 backend/../logs（项目根），曾与应用日志分裂在两个目录（P1-1）。
    _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    # 按天轮转、保留 7 天，避免明文流量日志无限增长
    _fh = _logging_handlers.TimedRotatingFileHandler(
        _os.path.join(_log_dir, "llm_traffic.log"),
        when="midnight", backupCount=7, encoding="utf-8",
    )
    _fh.setLevel(_logging.DEBUG)
    _fh.setFormatter(_logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _llm_logger.addHandler(_fh)
    _llm_logger.propagate = False


def _log_llm_request(call_id: str, provider: str, model: str, url: str, payload: dict):
    """记录 LLM 请求（结构化 JSON Lines，便于程序化分析）"""
    # 把 call_id 绑到当前上下文：调用方落可观测性事件时取出，
    # 于是 agent_events 的每条 llm_turn 都能回查到 llm_traffic.log 的同一次调用。
    # 放在这里而不是 4 个发请求点：本函数已经收到 call_id，一处绑定全覆盖。
    # 延迟导入避免 llm ←→ harness 包初始化环。
    try:
        from harness.observability.log_context import bind_call_id
        bind_call_id(call_id)
    except Exception:
        pass
    record = {
        "call_id": call_id,
        "dir": "request",
        "provider": provider,
        "model": model,
        "url": url,
        "payload": json.dumps(payload, ensure_ascii=False, default=str)[:8000],
    }
    _llm_logger.info(json.dumps(record, ensure_ascii=False, default=str))


def _log_llm_response(call_id: str, status: int, body: dict, duration_ms: float):
    """记录 LLM 响应（结构化 JSON Lines，便于程序化分析）"""
    record = {
        "call_id": call_id,
        "dir": "response",
        "status": status,
        "duration_ms": round(duration_ms),
        "body": json.dumps(body, ensure_ascii=False, default=str)[:8000],
    }
    _llm_logger.info(json.dumps(record, ensure_ascii=False, default=str))


def _sanitize_messages_for_replay(messages):
    """DeepSeek thinking 模式强制校验：assistant 消息回传时必须携带产生时的
    reasoning_content（实测 deepseek-v4-flash 报 400："The reasoning_content
    in the thinking mode must be passed back to the API"）。

    我们的对话历史（dialogue_history / SSE / DB）只存正文，无法回传思考链，
    因此发送前把缺 reasoning_content 的 assistant 历史降级为 user 角色上下文：
    内容完整保留、仅角色降级并加来源标注；连续 user 消息合并以兼容
    Anthropic 等要求严格交替的协议。带 reasoning_content 或 tool_calls 的
    消息原样保留。
    """
    if not isinstance(messages, list):
        return messages
    sanitized = []
    for m in messages:
        if (isinstance(m, dict) and m.get('role') == 'assistant'
                and not m.get('reasoning_content')):
            if m.get('tool_calls'):
                sanitized.append(m)
                continue
            sanitized.append({
                'role': 'user',
                'content': (
                    "[历史助手产出（供上下文参考，非本轮指令）]\n"
                    + str(m.get('content', ''))
                ),
            })
        else:
            sanitized.append(m)
    # 合并连续 user 消息（协议兼容 + 减少消息条数）
    merged = []
    for m in sanitized:
        if (merged and isinstance(m, dict) and m.get('role') == 'user'
                and merged[-1].get('role') == 'user'
                and isinstance(m.get('content'), str)
                and isinstance(merged[-1].get('content'), str)):
            merged[-1] = dict(merged[-1])
            merged[-1]['content'] = merged[-1]['content'] + '\n\n' + m['content']
        else:
            merged.append(m)
    return merged


def _log_and_raise(response, call_id: str = 'unknown', t0=None):
    """非 2xx 时先把响应体写入流量日志再抛出异常。

    此前 HTTPError 只留下 "400 Client Error" 级别的摘要，provider 给出的
    关键校验原因（如 reasoning_content 回传要求）全部丢失，只能靠抓包定位。
    """
    try:
        status = int(response.status_code)
    except (TypeError, ValueError):
        # status_code 非数值（如测试 Mock），退回原始行为
        response.raise_for_status()
        return
    if status >= 400:
        try:
            body = {"error_body": response.text[:2000]}
        except Exception:
            body = {"error_body": "<unreadable>"}
        duration_ms = (time.time() - t0) * 1000 if t0 else 0.0
        _log_llm_response(call_id, response.status_code, body, duration_ms)
    response.raise_for_status()


# ==================== HTTP 错误分类：决定「重试」还是「立即失败」 ====================
# 为什么必须分类：401/403 是配置问题（key 失效 / 无权限 / 套餐被禁用），服务端已经
# 明确拒绝，重试用的是同一个 key，必然复现。此前 chat() 路径把它当普通瞬时错误重试，
# 日志里堆出成百上千行 401，前端只看到「已达最大重试次数」，完全看不出根因是 key
# 失效——2026-09-28 实测单日 229 次 401，全部是 `无效的令牌`，而同一时刻用同一个
# key 手工请求是 200，说明是运行进程持有的 key 已经过期/被替换。
# 429（限流）则相反：它是**会自己恢复**的，且免费额度下很容易被自己的批量任务打出来，
# 必须重试，而且退避要比普通网络抖动长得多（默认指数退避上限 10s 对按分钟计的限流太短）。
_AUTH_STATUS = (401, 403)
_RATE_LIMIT_STATUS = 429


def key_fingerprint(key) -> str:
    """密钥指纹：前 6 + 后 4 + 长度。

    刻意不输出完整 key：既能回答"是哪个 key、有没有换错"，又不构成泄漏。
    """
    if not key:
        return "<空>"
    val = str(key).strip()
    if len(val) <= 12:
        return f"<过短 len={len(val)}>"
    return f"{val[:6]}...{val[-4:]}(len={len(val)})"


def classify_http_status(status):
    """把 HTTP 状态码映射为处理策略。

    Returns:
        'auth'       —— 401/403：配置问题，禁止重试
        'rate_limit' —— 429：可恢复，用长退避重试
        'transient'  —— 5xx / 408：正常退避重试
        'client'     —— 其它 4xx：参数/协议问题，禁止重试
        None         —— 取不到状态码（纯网络层异常），按 transient 处理
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return None
    if code in _AUTH_STATUS:
        return 'auth'
    if code == _RATE_LIMIT_STATUS:
        return 'rate_limit'
    if 500 <= code < 600 or code == 408:
        return 'transient'
    if 400 <= code < 500:
        return 'client'
    return None


def _status_of(exc) -> Optional[int]:
    """从 requests 异常里取 HTTP 状态码；取不到返回 None。"""
    resp = getattr(exc, 'response', None)
    try:
        return int(getattr(resp, 'status_code', None))
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(exc) -> Optional[float]:
    """读取服务端 Retry-After 响应头（秒）。

    限流时服务端给出的等待时间比任何自算退避都可信；拿不到再退回自算。
    """
    resp = getattr(exc, 'response', None)
    if resp is None:
        return None
    try:
        raw = resp.headers.get('Retry-After')
    except Exception:
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _llm_error_message(exc, client=None, status: Optional[int] = None) -> str:
    """把底层异常翻译成「人能直接照着修」的错误文案。

    此前所有失败都长成 `[错误] API 请求失败：401 Client Error: Unauthorized for url: ...`，
    读者无法区分"key 失效"、"没权限"、"被限流"、"端点挂了"。这里按类别给出下一步动作，
    并且只暴露 key 指纹（不泄漏完整密钥）。
    """
    code = status if status is not None else _status_of(exc)
    kind = classify_http_status(code)
    base_url = getattr(client, 'base_url', None) or '?'
    model = getattr(client, 'model', None) or '?'
    vendor = getattr(client, 'vendor', None) or 'auto'
    fingerprint = key_fingerprint(getattr(client, 'api_key', '') if client else '')

    if kind == 'auth':
        return (
            f"[错误] LLM 鉴权失败（HTTP {code}）：当前 API Key 被服务端拒绝，重试无效。\n"
            f"  · 端点：{base_url}（厂商配置 vendor={vendor}，模型 {model}）\n"
            f"  · 当前 Key 指纹：{fingerprint}\n"
            f"  · 排查：① Key 是否已过期/被重置 ② 是否配了与端点不匹配的 Key"
            f"（不同渠道的 Key 不通用）③ 套餐是否被禁用。\n"
            f"  改完 Key 后需重启后端进程才会生效（配置在进程启动时读取）。"
        )
    if kind == 'rate_limit':
        retry_after = _retry_after_seconds(exc)
        hint = f"，服务端建议等待 {retry_after:.0f}s" if retry_after else ""
        return (
            f"[错误] LLM 被限流（HTTP 429）{hint}：账号额度/速率已打满。\n"
            f"  · 端点：{base_url}，模型 {model}\n"
            f"  · 排查：降低并发或放慢请求（批量任务加任务间隔），或提升套餐额度。"
        )
    if kind == 'client':
        return (
            f"[错误] LLM 请求参数被拒（HTTP {code}）：属于请求体/协议问题，重试无效。\n"
            f"  · 端点：{base_url}，模型 {model}，vendor={vendor}\n"
            f"  · 原始响应：{str(exc)[:300]}"
        )
    return f"[错误] LLM 调用失败：{str(exc)[:300]}"


def _try_fix_json(raw: str) -> dict | None:
    """尝试修复 LLM 返回的不完整 JSON"""
    # 方法1: 补齐末尾的 } 和 "
    stack = []
    in_str = False
    escaped = False
    for ch in raw:
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in '{[':
            stack.append(ch)
        elif not in_str and ch in '}]':
            if stack and ((ch == '}' and stack[-1] == '{') or (ch == ']' and stack[-1] == '[')):
                stack.pop()
    # 补齐
    fixed = raw.rstrip()
    if in_str:
        fixed += '"'
    for opener in reversed(stack):
        fixed += '}' if opener == '{' else ']'
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 方法2: 去掉最后不完整的字段（查找最后一个 ",
    last_quote = fixed.rfind('",')
    if last_quote > 0:
        try:
            return json.loads(fixed[:last_quote + 1] + '}')
        except json.JSONDecodeError:
            pass
    # 方法3: 从末尾去掉不完整 token，找到最后一个顶层逗号截断
    # 适用于截断位置在 key 开始处的情况（如 ..., "tech_stack": {...}, "）
    last_top_comma = -1
    depth = 0
    in_str3 = False
    escaped3 = False
    for i, ch in enumerate(raw):
        if escaped3:
            escaped3 = False
            continue
        if ch == '\\':
            escaped3 = True
            continue
        if ch == '"':
            in_str3 = not in_str3
        elif not in_str3:
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
            elif ch == ',' and depth == 1:
                last_top_comma = i
    if last_top_comma > 0:
        truncated = raw[:last_top_comma].rstrip()
        # 补齐未闭合的括号
        truncated_depth = 0
        in_ts = False
        esc_ts = False
        for ch in truncated:
            if esc_ts:
                esc_ts = False
                continue
            if ch == '\\':
                esc_ts = True
                continue
            if ch == '"':
                in_ts = not in_ts
            elif not in_ts:
                if ch in '{[':
                    truncated_depth += 1
                elif ch in '}]':
                    truncated_depth -= 1
        for _ in range(truncated_depth):
            truncated += '}'
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass
    # 方法4: 从右向左扫描，尝试在每个 '}' 位置截断并解析
    # 适用于 JSON 之后有额外非 JSON 文本的 LLM 响应
    for end_try in range(len(raw) - 1, max(0, len(raw) - 500), -1):
        if raw[end_try] == '}':
            try:
                return json.loads(raw[:end_try + 1])
            except json.JSONDecodeError:
                pass
    # 方法5: 截断在 key: 后面没有 value（如 {"key":, {"a": 1, "b":）
    # 移除最后一个不完整的 key-value 对，然后补全括号
    last_colon = raw.rfind('":')
    if last_colon > 0:
        # 往前找到这个 key 之前的最后一个分隔符
        # 可能是 ','（同层前一个字段之后）或 '{'（对象开头/嵌套对象开头）
        prev_comma = raw.rfind(',', 0, last_colon)
        prev_brace = raw.rfind('{', 0, last_colon)
        key_start = max(prev_comma, prev_brace)
        if key_start >= 0:
            if raw[key_start] == ',':
                # 截掉逗号及之后的不完整 key-value
                prefix = raw[:key_start]
            else:
                # key_start 指向 {，保留 { 并截掉其后的不完整 key
                prefix = raw[:key_start + 1]
            # 补全未闭合的括号
            depth = 0
            in_s = False
            esc = False
            for ch in prefix:
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"':
                    in_s = not in_s
                elif not in_s:
                    if ch in '{[':
                        depth += 1
                    elif ch in '}]':
                        depth -= 1
            prefix += '}' * depth
            try:
                return json.loads(prefix)
            except json.JSONDecodeError:
                pass
    return None


@dataclass
class Message:
    """消息对象"""
    role: str  # "system", "user", "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@dataclass
class ToolCall:
    """LLM 工具调用"""
    name: str
    arguments: dict


@dataclass(frozen=True)
class _Endpoint:
    """一次请求实际使用的端点参数（不可变）

    failover 时通过参数传递备用端点，而不是改写单例实例的
    base_url/api_key/model/provider——后者在多线程共享下会产生竞态，
    导致并发请求被发到错误的模型/协议/密钥。
    """
    provider: str
    base_url: str
    api_key: str
    model: str


@dataclass
class LLMResponse:
    """LLM 响应对象"""
    content: str
    reasoning_content: Optional[str] = None  # DeepSeek 等模型的思考链
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass
class _LLMMeta:
    """一次 LLM 调用的元数据回写容器（usage / finish_reason）。

    `_request_openai` / `_request_anthropic` 是**生成器**（与流式路径同构），
    生成器没有 return 值，元数据没法随 content 一起带出来 —— 于是历史上
    `chat()` 只能写死 `usage=None, finish_reason=None`（client.py:1156），
    planning/verifying/repairing 三阶段的 token 与成本在后台恒为 0，
    「截断 / 空响应 / 端点故障」三态也无法分诊。

    故改由调用方传入本容器，请求层解析完响应后回写。线程安全：容器由调用方
    自己创建，不共享。
    """
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None


class CircuitBreakerOpenError(Exception):
    """熔断器打开时抛出，调用方应捕获并做降级处理"""
    pass


class CircuitBreaker:
    """LLM 调用熔断器

    连续失败 N 次 → 熔断器打开 → 后续调用直接抛出 CircuitBreakerOpenError
    熔断器打开后等待 M 秒 → 半开状态 → 允许 1 次探测调用
    探测成功 → 关闭熔断器；探测失败 → 重新打开

    设计原则：
    - 快速失败优于长时间等待（fail fast > wait long）
    - 避免在 LLM API 不可用时持续消耗系统资源（线程、SSE 连接、用户耐心）
    - 半开状态允许自动恢复，无需人工干预
    """

    def __init__(self, threshold: int = 5, recovery_timeout: float = 30.0):
        self.threshold = threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed | open | half_open
        self._lock = threading.Lock()
        self._probe_in_flight = False  # 半开状态只放行一个探测请求

    @property
    def is_open(self) -> bool:
        """只读状态查询（不触发状态迁移，无副作用）"""
        with self._lock:
            return self._state == "open"

    def record_success(self):
        """调用成功，重置熔断器"""
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._probe_in_flight = False

    def record_failure(self):
        """调用失败，递增计数"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._probe_in_flight = False
            if self._failure_count >= self.threshold:
                self._state = "open"
                logger.warning(
                    f"[CircuitBreaker] 连续失败 {self._failure_count} 次，熔断器打开，"
                    f"将在 {self.recovery_timeout:.0f}s 后进入半开状态"
                )

    def check(self):
        """检查熔断器状态；打开时抛异常；半开时只放行一个探测请求（线程安全）"""
        with self._lock:
            if self._state == "closed":
                return
            if self._state == "open":
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = "half_open"
                    self._probe_in_flight = False
                else:
                    remaining = self.recovery_timeout - (time.time() - self._last_failure_time)
                    raise CircuitBreakerOpenError(
                        f"LLM 熔断器已打开（连续失败 {self._failure_count} 次），"
                        f"约 {remaining:.0f}s 后自动恢复"
                    )
            # half_open: 只允许一个探测请求通过
            if self._probe_in_flight:
                raise CircuitBreakerOpenError(
                    "LLM 熔断器处于半开状态，已有探测请求在途，请稍后重试"
                )
            self._probe_in_flight = True


class LLMClient:
    """
    统一 LLM 客户端

    支持两种 API 协议，通过 provider 参数切换：
    - openai_compatible:    POST {base_url}/chat/completions
                            Header: Authorization: Bearer {api_key}
                            system prompt 放在 messages 数组中
    - anthropic_compatible: POST {base_url}/messages
                            Header: x-api-key: {api_key}
                            system prompt 作为顶层字段
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None
    ):
        self.api_key = api_key or LLM_API_KEY
        self.base_url = base_url or LLM_BASE_URL
        self.model = model or LLM_MODEL
        self.provider = provider or LLM_PROVIDER
        self.temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
        self.max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS
        self.reasoning_fallback_tokens = settings.LLM_REASONING_FALLBACK_TOKENS
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else settings.LLM_MAX_RETRIES

        # 思考模式：DeepSeek V4 默认开启且 effort=high，会消耗大量 reasoning token。
        # 默认关闭以提速；需要更高推理质量时通过 LLM_THINKING=enabled 开启。
        self.thinking = settings.LLM_THINKING
        self.reasoning_effort = settings.LLM_REASONING_EFFORT
        # 厂商与 thinking 参数格式（Agnes / DeepSeek 格式完全不同，见 _build_thinking_params）
        self.vendor = settings.LLM_VENDOR
        self.thinking_format = settings.LLM_THINKING_FORMAT
        self.thinking_budget_tokens = settings.LLM_THINKING_BUDGET_TOKENS
        # DeepSeek 硬性要求：带 tools 的请求里，历史 assistant 消息的 reasoning_content
        # 必须原样回传，否则 API 直接 400；Agnes 不要求。
        # 这里做成「显式开启 OR 厂商推断」：只要厂商是 deepseek 就强制回传 ——
        # echo=False + deepseek + tools 这个组合**必然 400**，不存在能跑通的场景，
        # 所以自动打开只可能消掉故障，不可能改变正常行为。
        # 2026-10-05 实测踩点：切换厂商时 .env 里**后面**那行
        # `LLM_THINKING_ECHO_REASONING=false` 会覆盖掉本厂商段里的 true
        # （dotenv 后者胜），于是「只改 base_url + model」的切换会在第一次
        # 带工具的调用上报 400。这类静默失效不该由使用者记住，交给代码兜。
        self.echo_reasoning = (
            settings.LLM_THINKING_ECHO_REASONING or self._resolve_vendor() == 'deepseek'
        )
        self.strip_invalid_params = settings.LLM_STRIP_INVALID_PARAMS

        # 熔断器：防止 LLM API 不可用时持续无效重试
        self._circuit_breaker = CircuitBreaker(
            threshold=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
            recovery_timeout=settings.LLM_CIRCUIT_BREAKER_TIMEOUT,
        )

        # 备份模型参数（可选）：主模型不可用时自动切换
        self.backup_base_url = settings.LLM_BACKUP_BASE_URL or None
        self.backup_model = settings.LLM_BACKUP_MODEL or None
        self.backup_api_key = settings.LLM_BACKUP_API_KEY or None
        self.backup_provider = settings.LLM_BACKUP_PROVIDER or None
        self._has_backup = all([
            self.backup_base_url, self.backup_model, self.backup_api_key
        ])
        self._backup_circuit_breaker = CircuitBreaker(
            threshold=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
            recovery_timeout=settings.LLM_CIRCUIT_BREAKER_TIMEOUT,
        ) if self._has_backup else None

        # 会话记忆
        self._messages: List[Message] = []

        if not self.api_key:
            raise ValueError("请配置 LLM_API_KEY 环境变量")

        if self.provider not in ('openai_compatible', 'anthropic_compatible'):
            raise ValueError(f"不支持的 LLM_PROVIDER: {self.provider}，可选值：openai_compatible, anthropic_compatible")

        if self._has_backup:
            logger.info(
                f"LLMClient 初始化：provider={self.provider}, model={self.model}, base_url={self.base_url}"
                f" | 备用: provider={self.backup_provider}, model={self.backup_model}"
            )
        else:
            logger.info(f"LLMClient 初始化：provider={self.provider}, model={self.model}, base_url={self.base_url}")

    def _primary_endpoint(self) -> _Endpoint:
        """主模型端点参数（线程安全：只读快照，不修改实例状态）"""
        return _Endpoint(
            provider=self.provider, base_url=self.base_url,
            api_key=self.api_key, model=self.model,
        )

    def _backup_endpoint(self) -> _Endpoint:
        """备用模型端点参数（未配置备份时返回 None）"""
        if not self._has_backup:
            return None
        return _Endpoint(
            provider=self.backup_provider, base_url=self.backup_base_url,
            api_key=self.backup_api_key, model=self.backup_model,
        )

    def clear_memory(self):
        """清空会话记忆"""
        self._messages.clear()
        logger.debug("LLM 会话记忆已清空")

    def load_memory(self, dialogue_history: List[Dict[str, Any]]):
        """从数据库加载对话历史"""
        self.clear_memory()
        for msg in dialogue_history:
            role = msg.get('role', 'user')
            if role == 'user':
                self._messages.append(Message(role='user', content=msg.get('content', '')))
            elif role in ('agent', 'assistant'):
                self._messages.append(Message(role='assistant', content=msg.get('content', '')))
            elif role == 'system':
                self._messages.append(Message(role='system', content=msg.get('content', '')))
        logger.debug(f"从数据库加载了 {len(self._messages)} 条对话历史")

    def get_memory(self) -> List[Dict[str, str]]:
        """获取会话记忆（格式化为 API 请求格式）"""
        return [{'role': m.role, 'content': m.content} for m in self._messages]

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = True,
        images: Optional[list] = None
    ) -> List[Dict[str, str]]:
        """构建消息列表

        Args:
            images: 可选的图片内容块 / URL 列表，会拼进 **user** 消息。
                    图片放 system 或 assistant 会被端点拒绝（HTTP 400）。
        """
        messages = []

        # 系统提示
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        # 历史记忆
        if use_memory:
            messages.extend(self.get_memory())

        # 用户输入（可选携带图片内容块）
        # 注意：图片只能出现在 user 消息里 —— 放进 system / assistant 会被
        # Agnes 与 DeepSeek 同时拒绝（HTTP 400）。故这里只拼 user。
        if images:
            content: list = [{"type": "text", "text": prompt}]
            for img in images:
                content.append(img if isinstance(img, dict) else {
                    "type": "image_url", "image_url": {"url": str(img)}
                })
            messages.append({'role': 'user', 'content': content})
        else:
            messages.append({'role': 'user', 'content': prompt})

        return messages

    def _request_openai(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        timeout: Optional[int] = None,
        endpoint: Optional[_Endpoint] = None,
        meta: Optional[_LLMMeta] = None
    ) -> Generator[str, None, None]:
        """发送 OpenAI 兼容 API 请求（带重试）

        endpoint: 目标端点参数；None 时使用主模型配置。
        timeout: 本次调用超时秒数；None 时使用实例默认。
        meta: 元数据回写容器（usage / finish_reason）；见 _LLMMeta 的说明。
        """
        ep = endpoint or self._primary_endpoint()
        effective_timeout = timeout if timeout is not None else self.timeout
        headers = {
            'Authorization': f'Bearer {ep.api_key}',
            'Content-Type': 'application/json'
        }

        # 思考模式：调用级覆盖 > 实例默认
        _thinking = thinking or self.thinking
        data = {
            'model': ep.model,
            'messages': _sanitize_messages_for_replay(messages),
            'stream': stream,
            'temperature': self.temperature,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens
        }
        # 思考模式开关：不同厂商参数格式完全不同，统一由 _build_thinking_params /
        # _build_thinking_disable_params 决定。**"关思考"绝不能靠省略字段**——
        # 省略在各端点语义不一致（实测矩阵，deepseek-v4-flash 于 2026-10-04 实测）：
        #   · deepseek-v4-flash：省略 = 服务端**默认开启**推理（实测 completion 89 token
        #     里 86 是 reasoning，正文只剩 3 token）；显式 {"type":"disabled"} = 真正
        #     关闭（reasoning 0，completion 2）。`chat_template_kwargs.enable_thinking
        #     =False` **无效**（仍 241 字符推理）。
        #   · agnes-3.0-flash：省略 = 思考 OFF；携带 thinking 字段（哪怕
        #     {"type":"disabled"}）反而**触发**思考 —— 与 DeepSeek 完全相反。
        #   · glm-5.3-flash（lkeap）：省略 = 服务端默认自动思考（关不掉），
        #     且 disabled 值直接 HTTP 400。
        # 故关闭动作必须按厂商分发，只有确认「显式关闭被正确接受」的厂商才发字段。
        if _thinking == 'enabled':
            data.update(self._build_thinking_params())
            # DeepSeek 思考模式下 temperature / presence_penalty / frequency_penalty
            # 不生效（不报错但静默无效），top_p 仅 0.95~1.0 有效。
            # 剔除掉，避免"以为调了参其实没生效"的误判。
            if self.strip_invalid_params and self._resolve_vendor() == 'deepseek':
                data.pop('temperature', None)
        else:
            data.update(self._build_thinking_disable_params())

        url = f'{ep.base_url}/chat/completions'

        for attempt in range(self.max_retries + 1):
            stream_yielded = False
            try:
                if stream:
                    response = requests.post(
                        url, headers=headers, json=data,
                        stream=True, timeout=effective_timeout
                    )
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if line:
                            line = line.decode('utf-8')
                            if line.startswith('data: '):
                                content = line[6:]
                                if content == '[DONE]':
                                    break
                                try:
                                    chunk = json.loads(content)
                                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                                    content_text = delta.get('content', '')
                                    # 注意：不 fallback 到 reasoning_content。
                                    # reasoning_content 是模型的内部思考链，不是最终回复。
                                    # reasoning 模型（如 DeepSeek-R1、agnes-2.0-flash）会同时
                                    # 流式输出 reasoning_content 和 content 两个 delta，
                                    # 我们只需要最终的 content。
                                    if content_text:
                                        stream_yielded = True
                                        yield content_text
                                except json.JSONDecodeError:
                                    continue
                else:
                    import uuid
                    call_id = uuid.uuid4().hex[:8]
                    t0 = time.time()
                    _log_llm_request(call_id, ep.provider, ep.model, url, data)
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=effective_timeout
                    )
                    _log_and_raise(response, call_id, t0)
                    result = response.json()
                    _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)
                    choice = result.get('choices', [{}])[0]
                    msg = choice.get('message', {})
                    content = msg.get('content', '')
                    reasoning = msg.get('reasoning_content', '')
                    # usage / finish_reason 必须带回上层：这是「截断=length」与
                    # 「真端点故障」唯一可靠的分诊依据，也是成本统计的唯一数据源。
                    if meta is not None:
                        meta.usage = result.get('usage') or meta.usage
                        meta.finish_reason = choice.get('finish_reason') or meta.finish_reason
                    # content 为空但有 reasoning_content：max_tokens 被 thinking 吃光了。
                    # 重试一次，但额度必须「够用且不过量」——
                    #   ① 必须显著大于原额度，否则同样被 thinking 再次吃光，重试无意义；
                    #   ② 必须有绝对天花板（LLM_REASONING_FALLBACK_TOKENS）。不能用全局
                    #      LLM_MAX_TOKENS：流水线里大量按需写死的小额度调用（500/1000/2000/3000）
                    #      一旦失败就会被抬到 32000，而该额度下 glm-5.3-flash 单轮要 ~680s
                    #      （cap=8000 实测 188s 全耗在思考上、正文 0 字），必然撞穿
                    #      LLM_TIMEOUT，整节点表现为「假挂死」。
                    if not content and reasoning:
                        req_tokens = data.get('max_tokens', 0) or 0
                        # ⚠️ 这里**不能**再夹一个 `self.max_tokens`（全局默认额度）：
                        # 主路径（coder / 评估 / 修复）取的正是全局默认值，于是
                        # 「req >= 全局默认」时 min() 必然回到 req 本身，
                        # `fallback_tokens <= req_tokens` 成立 → 救援被静默放弃。
                        # 实测代价（2026-10-05 req 222，deepseek-v4-flash +
                        # LLM_MAX_TOKENS=12000）：修复调用 max_tokens=12000 全被
                        # reasoning 吃光、content 为空、finish_reason=length，
                        # 救援本可抬到 16000 却直接放弃，最终整条定向修复被判
                        # 「端点连续 2 次不可用」而跳过。
                        # 绝对天花板由 LLM_REASONING_FALLBACK_TOKENS 单独承担
                        # —— 这正是它存在的意义（见上面 ② 的注释）。
                        # 已探测：deepseek-v4-flash 接受 16000 / 20000 不报错。
                        fallback_tokens = min(
                            max(req_tokens * 2, req_tokens + 4000),
                            self.reasoning_fallback_tokens,
                        )
                        if fallback_tokens <= req_tokens:
                            # 天花板已经压不住：再抬只会把一次超长调用变成必然超时。
                            # 不如把空内容如实交回上层（上层有自己的错误处理/降级）。
                            logger.error(
                                f"[LLM] reasoning 耗尽且重试额度无法提升 "
                                f"(reasoning={len(reasoning)} chars, content为空, "
                                f"req_max_tokens={req_tokens}, 可给上限={fallback_tokens})，"
                                f"放弃重试"
                            )
                            yield content
                            return
                        logger.warning(
                            f"[LLM] 检测到 reasoning 模型 token 耗尽 "
                            f"(reasoning={len(reasoning)} chars, content为空, "
                            f"req_max_tokens={req_tokens})，"
                            f"以 max_tokens={fallback_tokens} 重试"
                        )
                        retry_data = dict(data)
                        retry_data['max_tokens'] = fallback_tokens
                        # 救援重试此前完全不记 traffic log，也不记响应 ——
                        # 全链路看不到「曾经升档重试过」，排查时只能靠猜。
                        # 复用同一 call_id：与原始请求在日志里天然成对。
                        _log_llm_request(call_id, ep.provider, ep.model, url, retry_data)
                        retry_response = requests.post(
                            url, headers=headers, json=retry_data,
                            timeout=effective_timeout
                        )
                        _log_and_raise(retry_response, call_id, t0)
                        retry_result = retry_response.json()
                        _log_llm_response(call_id, retry_response.status_code,
                                          retry_result, (time.time() - t0) * 1000)
                        retry_choice = retry_result.get('choices', [{}])[0]
                        retry_msg = retry_choice.get('message', {})
                        content = retry_msg.get('content', '')
                        if meta is not None:
                            meta.usage = retry_result.get('usage') or meta.usage
                            meta.finish_reason = (
                                retry_choice.get('finish_reason') or meta.finish_reason)
                        if not content:
                            logger.error(
                                f"[LLM] 以 max_tokens={fallback_tokens} 重试后 content 仍为空 "
                                f"(reasoning={len(retry_msg.get('reasoning_content', ''))} chars)，"
                                f"交由上层错误处理"
                            )
                    yield content
                return

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                # 流式传输中途失败且已产出内容 → 不重试，避免前缀重复推送
                if stream and stream_yielded:
                    logger.error(f"LLM 流式传输中断（已产出部分内容，不重试）：{str(e)}")
                    yield f"[错误] 流式传输中断：{str(e)}"
                    return
                # 统一走分类退避：401/403 不重试、429 用长退避、其余短退避。
                # 此前这里是裸的内联退避，把 key 失效当网络抖动重试了 3 次。
                # 注意必须 return：这是循环体内的 yield，不 return 的话会因为
                # 落到下一轮迭代而继续发请求（"不重试"就名存实亡）。
                _status = _status_of(e)
                if self._retry_backoff(attempt, e, context="LLM", status=_status):
                    continue
                yield _llm_error_message(e, self, status=_status)
                return

    def _request_anthropic(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        timeout: Optional[int] = None,
        endpoint: Optional[_Endpoint] = None,
        meta: Optional[_LLMMeta] = None
    ) -> Generator[str, None, None]:
        """发送 Anthropic 兼容 API 请求（带重试）

        endpoint/timeout/meta 语义同 _request_openai。
        """
        ep = endpoint or self._primary_endpoint()
        effective_timeout = timeout if timeout is not None else self.timeout
        headers = {
            'x-api-key': ep.api_key,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Anthropic: system 是顶层字段，不在 messages 数组中
        system_prompt = None
        api_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_prompt = m['content']
            else:
                api_messages.append(m)

        data = {
            'model': ep.model,
            'messages': api_messages,
            'stream': stream,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens
        }
        # 思考模式开关（Anthropic 格式）：仅 enabled 时携带 reasoning 字段
        _thinking = thinking or self.thinking
        if _thinking == 'enabled':
            data['reasoning'] = {'effort': self.reasoning_effort}
        if system_prompt:
            data['system'] = system_prompt

        url = f'{ep.base_url}/messages'

        for attempt in range(self.max_retries + 1):
            stream_yielded = False
            try:
                if stream:
                    response = requests.post(
                        url, headers=headers, json=data,
                        stream=True, timeout=effective_timeout
                    )
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if line:
                            line = line.decode('utf-8')
                            if line.startswith('data: '):
                                content = line[6:]
                                try:
                                    chunk = json.loads(content)
                                    if chunk.get('type') == 'content_block_delta':
                                        delta = chunk.get('delta', {})
                                        text = delta.get('text', '')
                                        if text:
                                            stream_yielded = True
                                            yield text
                                    elif chunk.get('type') == 'message_stop':
                                        break
                                except json.JSONDecodeError:
                                    continue
                else:
                    import uuid
                    call_id = uuid.uuid4().hex[:8]
                    t0 = time.time()
                    _log_llm_request(call_id, ep.provider, ep.model, url, data)
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=effective_timeout
                    )
                    _log_and_raise(response, call_id, t0)
                    result = response.json()
                    _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)
                    content_blocks = result.get('content', [])
                    text = ''.join(
                        block.get('text', '')
                        for block in content_blocks
                        if block.get('type') == 'text'
                    )
                    if meta is not None:
                        meta.usage = result.get('usage') or meta.usage
                        # Anthropic 的停止原因字段名是 stop_reason，语义等价于
                        # OpenAI 的 finish_reason（length 同样表示被额度截断）。
                        meta.finish_reason = result.get('stop_reason') or meta.finish_reason
                    yield text
                return

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                # 流式传输中途失败且已产出内容 → 不重试，避免前缀重复推送
                if stream and stream_yielded:
                    logger.error(f"LLM 流式传输中断（已产出部分内容，不重试）：{str(e)}")
                    yield f"[错误] 流式传输中断：{str(e)}"
                    return
                # 统一走分类退避：401/403 不重试、429 用长退避、其余短退避。
                # 此前这里是裸的内联退避，把 key 失效当网络抖动重试了 3 次。
                # 注意必须 return：这是循环体内的 yield，不 return 的话会因为
                # 落到下一轮迭代而继续发请求（"不重试"就名存实亡）。
                _status = _status_of(e)
                if self._retry_backoff(attempt, e, context="LLM", status=_status):
                    continue
                yield _llm_error_message(e, self, status=_status)
                return

    def _do_request(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        timeout: Optional[int] = None,
        endpoint: Optional[_Endpoint] = None,
        meta: Optional[_LLMMeta] = None
    ) -> Generator[str, None, None]:
        """根据端点 provider 分发到对应的请求方法"""
        ep = endpoint or self._primary_endpoint()
        if ep.provider == 'anthropic_compatible':
            yield from self._request_anthropic(messages, stream, max_tokens=max_tokens,
                                               thinking=thinking, timeout=timeout,
                                               endpoint=ep, meta=meta)
        else:
            yield from self._request_openai(messages, stream, max_tokens=max_tokens,
                                            thinking=thinking, timeout=timeout,
                                            endpoint=ep, meta=meta)

    def _chat_request_loop(
        self, messages: list, effective_max_tokens: int, effective_timeout: int,
        thinking: Optional[str] = None,
        endpoint: Optional[_Endpoint] = None,
        meta: Optional[_LLMMeta] = None
    ) -> tuple:
        """单次请求（不在此层重试）。

        重试由 _request_openai/_request_anthropic 内层负责（带指数退避）。
        之前这里再加一层循环会导致 (max_retries+1)² 次请求放大。
        超时保护由底层 requests.post(timeout=...) 提供，支持主线程和工作线程。

        meta 传入时由底层回写 usage / finish_reason（见 _LLMMeta）。

        Returns:
            (content: str, error: str | None, failed: bool)
        """
        content = ""
        error = None
        failed = False
        try:
            for chunk in self._do_request(
                messages, stream=False,
                max_tokens=effective_max_tokens,
                thinking=thinking,
                timeout=effective_timeout,
                endpoint=endpoint,
                meta=meta,
            ):
                content = chunk

            if not content or content.startswith('[错误]'):
                failed = True
                if content.startswith('[错误]'):
                    error = content
                else:
                    error = "LLM 返回空响应"
        except Exception as e:
            error = str(e)
            failed = True
            content = f"[错误] 请求失败：{error}"

        return content, error, failed

    # ------------------------------------------------------------------
    # 多厂商 thinking 参数适配
    # ------------------------------------------------------------------
    def _resolve_vendor(self) -> str:
        """推断厂商：显式配置优先，否则按 base_url 关键字推断。

        Agnes 与 DeepSeek 的 thinking 参数格式完全不同（见 _build_thinking_params），
        必须先确定厂商才能选格式。
        """
        vendor = (self.vendor or 'auto').strip().lower()
        if vendor in ('agnes', 'deepseek'):
            return vendor
        url = (self.base_url or '').lower()
        if 'deepseek' in url:
            return 'deepseek'
        # 当前主用 Agnes，未识别时按 Agnes 处理（其端点对两套格式都兼容）
        return 'agnes'

    def _build_thinking_params(self) -> dict:
        """组装 thinking 请求参数（仅 thinking=enabled 时调用）。

        实测矩阵（agnes-3.0-flash，同 prompt isPrime，max_tokens=2000）：
          省略字段                                  → 无思考，1.95s
          thinking:{type:enabled}+reasoning_effort  → 思考 675c，3.49s
          thinking:{type:enabled,budget_tokens:1024}→ 思考 330c，2.79s（思考量砍半）
          chat_template_kwargs:{enable_thinking:1}  → 思考 635c，4.75s

        故 Agnes 默认走 anthropic 格式（可限流思考量），DeepSeek 走 openai 格式
        （其无 budget_tokens，强度靠顶层 reasoning_effort）。
        """
        vendor = self._resolve_vendor()
        fmt = (self.thinking_format or 'auto').strip().lower()
        if fmt == 'auto':
            # Agnes 用 anthropic 格式可拿到 budget_tokens 限流；DeepSeek 只能 openai 格式
            fmt = 'anthropic' if vendor == 'agnes' else 'openai'

        params: dict = {}
        if vendor == 'deepseek':
            if fmt == 'anthropic':
                # DeepSeek Anthropic 兼容：reasoning.effort，none 表示关闭
                params['reasoning'] = {'effort': self.reasoning_effort}
            else:
                # DeepSeek OpenAI 兼容：thinking.type + 顶层 reasoning_effort
                params['thinking'] = {'type': 'enabled'}
                params['reasoning_effort'] = self.reasoning_effort
        else:
            if fmt == 'openai':
                params['chat_template_kwargs'] = {'enable_thinking': True}
            else:
                thinking_obj: dict = {'type': 'enabled'}
                if self.thinking_budget_tokens and self.thinking_budget_tokens > 0:
                    thinking_obj['budget_tokens'] = int(self.thinking_budget_tokens)
                params['thinking'] = thinking_obj
                params['reasoning_effort'] = self.reasoning_effort
        return params

    def _build_thinking_disable_params(self) -> dict:
        """组装「关闭思考」的请求参数（仅 thinking != enabled 时调用）。

        默认返回空 dict（= 省略字段），因为多数端点省略即关闭。只有**实证确认
        省略关不掉、且显式关闭字段被正确接受**的厂商才返回非空：

        · deepseek：省略时服务端默认推理（实测 86/89 token 是 reasoning），
          显式 `{"thinking": {"type": "disabled"}}` 后 reasoning 归零。
          （`reasoning_effort: "none"` 实测同样有效，但 thinking 字段是 OpenAI
            兼容端点的正统写法，优先用它。）
        · agnes：省略即关闭，发字段反而触发思考 → 必须保持省略。
        · glm/lkeap：省略关不掉，但 disabled 值直接 HTTP 400 → 只能保持省略
          （换模型/端点才是解法）。
        """
        if self._resolve_vendor() != 'deepseek':
            return {}
        return {'thinking': {'type': 'disabled'}}

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = False,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        thinking: Optional[str] = None,
        images: Optional[list] = None
    ) -> LLMResponse:
        """
        非流式聊天

        支持主备模型自动切换：主模型不可用时（熔断器打开或请求失败），
        自动切换到备用模型重试。

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆（默认 False：单例客户端被多线程共享，
                开启记忆会导致跨请求串扰，仅在单线程场景显式开启）
            max_tokens: 最大生成 token 数（覆盖默认值）
            timeout: 超时时间（覆盖默认值）
            thinking: 思考模式覆盖（'enabled'/'disabled'，默认 None 使用实例配置）
            images: 可选图片（URL 字符串 / data URI / 内容块 dict），
                    会以 image_url 内容块拼进 user 消息，用于多模态评估。
                    注意：图片只能出现在 user 消息，放 system 会被端点拒绝。

        Returns:
            LLMResponse 对象
        """
        # 解析本次调用的有效参数（不修改实例状态，保证线程安全）
        effective_max_tokens = max_tokens or self.max_tokens
        effective_timeout = timeout or self.timeout

        messages = self._build_messages(prompt, system_prompt, use_memory, images=images)
        logger.debug(f"LLM 请求：messages_count={len(messages)}, max_tokens={effective_max_tokens}")

        content = ""
        error = None
        tried_backup = False

        # usage / finish_reason 由底层请求层回写（生成器带不出 return 值），
        # 主备两次尝试共用一个容器：备用成功时以备用为准覆盖。
        meta = _LLMMeta()

        # ---- 主模型尝试 ----
        primary_available = True
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError as e:
            primary_available = False
            logger.warning(f"[Failover] 主模型熔断器已打开: {e}")

        if primary_available:
            content, error, failed = self._chat_request_loop(
                messages, effective_max_tokens, effective_timeout, thinking,
                endpoint=self._primary_endpoint(), meta=meta
            )
            if failed and (not content or content.startswith('[错误]')):
                self._circuit_breaker.record_failure()
            elif content and not content.startswith('[错误]'):
                self._circuit_breaker.record_success()

        # ---- 主模型失败 → 尝试备用模型 ----
        backup_ep = self._backup_endpoint()
        if (not content or content.startswith('[错误]')) and backup_ep:
            logger.warning(
                f"[Failover] 主模型失败，切换到备用模型 "
                f"({backup_ep.provider}/{backup_ep.model}@{backup_ep.base_url})"
            )
            try:
                self._backup_circuit_breaker.check()
            except CircuitBreakerOpenError as e:
                logger.error(f"[Failover] 备用模型熔断器也已打开: {e}")
            else:
                # 端点经参数传递，不改写实例状态（多线程共享单例下安全）
                content, error, failed = self._chat_request_loop(
                    messages, effective_max_tokens, effective_timeout, thinking,
                    endpoint=backup_ep, meta=meta
                )
                if failed and (not content or content.startswith('[错误]')):
                    self._backup_circuit_breaker.record_failure()
                elif content and not content.startswith('[错误]'):
                    self._backup_circuit_breaker.record_success()
                    tried_backup = True
                    logger.info("[Failover] 备用模型请求成功")

        # 保存到记忆
        if use_memory and content and not content.startswith('[错误]'):
            self._messages.append(Message(role='user', content=prompt))
            self._messages.append(Message(role='assistant', content=content))

        # 用量与停止原因：此前这里硬编码 None，导致 planning / verifying / repairing
        # 三个只走 chat() 的阶段在后台 token 与成本恒为 0（成本数据不可信），且
        # 「被额度截断」与「端点故障」无法区分。现在如实带回底层观测到的事实。
        return LLMResponse(content=content, usage=meta.usage,
                           finish_reason=meta.finish_reason, error=error)

    def chat_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = False
    ) -> Generator[str, None, None]:
        """
        流式聊天，含主备模型自动切换

        注意：由于流式特性，只能在请求开始前判断主备（熔断器级别切换）。
        如果流式传输中途失败，本次请求无法重试。
        use_memory 默认 False：单例客户端被多线程共享，开启记忆会跨请求串扰
        （与 chat() 保持一致；单线程场景可显式开启）。

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆

        Yields:
            文本片段
        """
        messages = self._build_messages(prompt, system_prompt, use_memory)
        logger.debug(f"LLM 流式请求：messages_count={len(messages)}")

        # 选择模型：主模型可用 → 主；主不可用 + 有备份 → 备
        use_backup = False
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError:
            if self._has_backup:
                try:
                    self._backup_circuit_breaker.check()
                    use_backup = True
                    logger.warning("[Failover] 主模型熔断器打开，流式请求使用备用模型")
                except CircuitBreakerOpenError as e:
                    logger.error(f"[Failover] 主备模型熔断器均已打开: {e}")
                    yield f"[错误] LLM 服务不可用：{e}"
                    return
            else:
                yield "[错误] LLM 熔断器已打开，请稍后重试"
                return

        # 流式请求
        stream_ok = False
        backup_ep = self._backup_endpoint()
        if use_backup and backup_ep:
            full_content = ""
            for chunk in self._do_request(messages, stream=True, endpoint=backup_ep):
                if chunk:
                    full_content += chunk
                    stream_ok = True
                    yield chunk

            if stream_ok and not full_content.startswith('[错误]'):
                self._backup_circuit_breaker.record_success()
            else:
                self._backup_circuit_breaker.record_failure()
        else:
            full_content = ""
            for chunk in self._do_request(messages, stream=True):
                if chunk:
                    full_content += chunk
                    stream_ok = True
                    yield chunk

            if stream_ok and not full_content.startswith('[错误]'):
                self._circuit_breaker.record_success()
            elif not stream_ok or full_content.startswith('[错误]'):
                self._circuit_breaker.record_failure()

        # 保存到记忆
        if use_memory and full_content and not full_content.startswith('[错误]'):
            self._messages.append(Message(role='user', content=prompt))
            self._messages.append(Message(role='assistant', content=full_content))


    def _chat_with_tools_request_loop(
        self, messages: list, tools: list, tool_choice: str, effective_max_tokens: int,
        thinking: Optional[str] = None,
        timeout: Optional[int] = None,
        endpoint: Optional[_Endpoint] = None,
        max_retries: Optional[int] = None,
        meta: Optional[_LLMMeta] = None,
    ) -> tuple:
        """chat_with_tools 核心请求+重试循环

        重试策略：指数退避；HTTP 4xx（除 429）为非瞬时错误，立即终止不重试。

        Args:
            max_retries: 覆盖实例级重试次数。调用方做长尾熔断时传 0——
                超时预算是给"这一次尝试"的，若内部再重试 N 次，
                实际墙钟时间会变成 N+1 倍预算，熔断就失去了意义。
            meta: 元数据回写容器（usage / finish_reason）；见 _LLMMeta。

        Returns:
            (content, reasoning_content, tool_calls, usage, failed)
        """
        ep = endpoint or self._primary_endpoint()
        effective_timeout = timeout if timeout is not None else self.timeout
        content = ""
        reasoning_content = None
        tool_calls = None
        usage = None
        failed = False

        effective_retries = self.max_retries if max_retries is None else max_retries
        for attempt in range(effective_retries + 1):
            try:
                if ep.provider == 'anthropic_compatible':
                    content, reasoning_content, tool_calls, usage = self._request_anthropic_with_tools(
                        messages, tools, max_tokens=effective_max_tokens, thinking=thinking,
                        tool_choice=tool_choice, timeout=effective_timeout, endpoint=ep,
                        meta=meta)
                else:
                    content, reasoning_content, tool_calls, usage = self._request_openai_with_tools(
                        messages, tools, tool_choice, max_tokens=effective_max_tokens, thinking=thinking,
                        timeout=effective_timeout, endpoint=ep, meta=meta)
                break
            except requests.exceptions.HTTPError as e:
                status = _status_of(e)
                kind = classify_http_status(status)
                if kind in ('auth', 'client'):
                    # 鉴权/参数/协议类错误，重试必然复现 → 立即失败并给可执行提示
                    err = _llm_error_message(e, self, status=status)
                    logger.error(f"chat_with_tools 非可重试错误(HTTP {status})，不再重试：{e}")
                    failed = True
                    content = err
                    break
                failed = True
                if self._retry_backoff(attempt, e, effective_retries,
                                       context="chat_with_tools", status=status):
                    continue
                content = _llm_error_message(e, self, status=status)
            except Exception as e:
                failed = True
                if self._retry_backoff(attempt, e, effective_retries,
                                       context="chat_with_tools", status=_status_of(e)):
                    continue
                content = _llm_error_message(e, self, status=_status_of(e))

        return content, reasoning_content, tool_calls, usage, failed

    def _retry_backoff(self, attempt: int, e: Exception,
                       max_retries: Optional[int] = None, context: str = "LLM",
                       status: Optional[int] = None) -> bool:
        """重试退避：还有剩余次数则等待并返回 True（继续重试），否则返回 False

        按错误类别分档（这是"该不该重试"的唯一判定点，避免各调用点各写一套）：
          · auth(401/403)：**永不重试**。同一个 key 重试多少次都是同一个拒绝，
            白等还会把真正的原因埋在一堆重试日志里。
          · rate_limit(429)：优先采用服务端 Retry-After，否则用更长的专用退避
            （LLM_RATE_LIMIT_BACKOFF_BASE_S / _MAX_S）——默认的 10s 上限对按分钟
            计费的限流窗口太短，等于"重试了但一定还失败"。
          · 其它（网络抖动/5xx）：保持原有短指数退避。

        Args:
            max_retries: 覆盖实例级重试上限（长尾熔断时用 0 关闭重试）
            status: HTTP 状态码（网络层异常时为 None）
        """
        kind = classify_http_status(status)
        limit = self.max_retries if max_retries is None else max_retries

        if kind == 'auth':
            # 只记原始原因（短），可执行的排查提示由调用方通过
            # _llm_error_message 统一生成并回给上层 —— 两边都拼一遍文案
            # 会在日志里出现"鉴权失败…立即终止：鉴权失败…"的重复。
            logger.error(
                f"{context} 鉴权失败(HTTP {status})，重试不会改变结果，立即终止：{str(e)[:200]}"
            )
            return False
        if kind == 'client':
            logger.error(
                f"{context} 请求被拒(HTTP {status})，属参数/协议问题，立即终止：{str(e)[:300]}"
            )
            return False

        if attempt >= limit:
            logger.error(f"{context} 失败：{e}（已用满 {limit} 次重试）")
            return False

        if kind == 'rate_limit':
            server_wait = _retry_after_seconds(e)
            if server_wait is not None:
                delay = server_wait
            else:
                import random
                base = max(0.1, float(getattr(settings, 'LLM_RATE_LIMIT_BACKOFF_BASE_S', 5.0)))
                cap = max(base, float(getattr(settings, 'LLM_RATE_LIMIT_BACKOFF_MAX_S', 60.0)))
                delay = min(base * (2 ** attempt), cap) * (0.7 + random.random() * 0.6)
            logger.warning(
                f"{context} 被限流(HTTP 429)，{delay:.1f}秒后重试 "
                f"({attempt + 1}/{limit})；若频繁出现请降低并发/加快任务间隔"
            )
        else:
            import random
            delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random() * 0.5)
            logger.warning(f"{context} 失败：{e}，{delay:.2f}秒后重试 ({attempt + 1}/{limit})")
        time.sleep(delay)
        return True

    def chat_with_tools(
        self,
        messages: list,
        tools: list,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> LLMResponse:
        """
        支持 function calling 的聊天接口，含主备模型自动切换

        Args:
            messages: 消息列表 [{"role": "...", "content": "..."}]
            tools: 工具描述列表（OpenAI function calling 格式）
            tool_choice: "auto" / "none" / "required"
            max_tokens: 最大 token 数
            thinking: 思考模式覆盖（'enabled'/'disabled'，默认 None 使用实例配置）
            timeout: 单次请求超时（秒），None 表示使用实例默认值。
                     调用方可用它给单轮 LLM 设预算，实现长尾熔断。
            max_retries: 覆盖实例级重试次数，None 表示使用实例默认值。

        Returns:
            LLMResponse 含 tool_calls 字段
        """
        # 线程安全：不修改 self.max_tokens，按调用解析有效值
        effective_max_tokens = max_tokens or self.max_tokens

        content = ""
        reasoning_content = None
        tool_calls = None
        usage = None
        finish_reason = None
        error = None

        # 与 chat() 同构：finish_reason 由底层回写（此前恒为 None，
        # 「内容被截断」与「正常结束」在编码阶段同样无法区分）。
        meta = _LLMMeta()

        # ---- 主模型尝试 ----
        primary_available = True
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError as e:
            primary_available = False
            logger.warning(f"[Failover] 主模型熔断器已打开: {e}")

        if primary_available:
            content, reasoning_content, tool_calls, usage, failed = \
                self._chat_with_tools_request_loop(
                    messages, tools, tool_choice, effective_max_tokens, thinking,
                    timeout=timeout, endpoint=self._primary_endpoint(),
                    max_retries=max_retries, meta=meta,
                )
            if failed and (not content or content.startswith('[错误]')):
                self._circuit_breaker.record_failure()
            elif content and not content.startswith('[错误]'):
                self._circuit_breaker.record_success()

        # ---- 主模型失败 → 尝试备用模型 ----
        backup_ep = self._backup_endpoint()
        if (not content or content.startswith('[错误]')) and backup_ep:
            logger.warning(
                f"[Failover] 主模型失败，切换到备用模型 "
                f"({backup_ep.provider}/{backup_ep.model}@{backup_ep.base_url})"
            )
            try:
                self._backup_circuit_breaker.check()
            except CircuitBreakerOpenError as e:
                logger.error(f"[Failover] 备用模型熔断器也已打开: {e}")
            else:
                # 端点经参数传递，不改写实例状态（多线程共享单例下安全）
                content, reasoning_content, tool_calls, usage, failed = \
                self._chat_with_tools_request_loop(
                    messages, tools, tool_choice, effective_max_tokens, thinking,
                    timeout=timeout, endpoint=backup_ep, max_retries=max_retries,
                    meta=meta,
                )
                if failed and (not content or content.startswith('[错误]')):
                    self._backup_circuit_breaker.record_failure()
                elif content and not content.startswith('[错误]'):
                    self._backup_circuit_breaker.record_success()
                    logger.info("[Failover] 备用模型 chat_with_tools 请求成功")

        # 错误语义与 chat() 对齐：请求失败时必须设置 error，
        # 否则调用方（runtime.is_error 分支）会把 LLM 故障误判为任务正常完成。
        # 注意：tool-only 响应（content 为空但带 tool_calls，finish_reason=tool_calls）
        # 是 thinking 模型的常态输出，不是空响应——此前被误杀导致整轮编码中断
        if not error:
            if content.startswith('[错误]'):
                error = content
            elif not content and not tool_calls:
                error = "LLM 返回空响应"

        return LLMResponse(content=content, reasoning_content=reasoning_content,
                           usage=usage, finish_reason=meta.finish_reason,
                           error=error, tool_calls=tool_calls)

    def _request_openai_with_tools(self, messages: list, tools: list, tool_choice: str,
                                    max_tokens: Optional[int] = None,
                                    thinking: Optional[str] = None,
                                    timeout: Optional[int] = None,
                                    endpoint: Optional[_Endpoint] = None,
                                    meta: Optional[_LLMMeta] = None):
        """OpenAI function calling 协议（meta 语义同 _request_openai）"""
        import uuid
        ep = endpoint or self._primary_endpoint()
        effective_timeout = timeout if timeout is not None else self.timeout
        headers = {
            'Authorization': f'Bearer {ep.api_key}',
            'Content-Type': 'application/json'
        }
        # 思考模式：调用级覆盖 > 实例默认
        _thinking = thinking or self.thinking
        data = {
            'model': ep.model,
            'messages': _sanitize_messages_for_replay(messages),
            'temperature': self.temperature,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens,
            'tools': tools,
            'tool_choice': tool_choice,
        }
        # 思考模式开关（OpenAI 格式，DeepSeek 扩展字段）：语义同 _request_openai。
        # 关闭必须走 _build_thinking_disable_params —— 省略字段在 DeepSeek 上
        # 关不掉（服务端默认推理），各端点语义矩阵见 _request_openai 的注释。
        if _thinking == 'enabled':
            data['thinking'] = {'type': _thinking}
            data['reasoning_effort'] = self.reasoning_effort
        else:
            data.update(self._build_thinking_disable_params())
        url = f'{ep.base_url}/chat/completions'

        call_id = uuid.uuid4().hex[:8]
        t0 = time.time()
        _log_llm_request(call_id, ep.provider, ep.model, url, data)

        response = requests.post(url, headers=headers, json=data, timeout=effective_timeout)
        _log_and_raise(response, call_id, t0)
        result = response.json()

        _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)

        choice = result.get('choices', [{}])[0]
        msg = choice.get('message', {})
        content = msg.get('content', '') or ''
        reasoning_content = msg.get('reasoning_content', '') or None
        usage_data = result.get('usage')
        if meta is not None:
            meta.usage = usage_data or meta.usage
            meta.finish_reason = choice.get('finish_reason') or meta.finish_reason

        # 如果 content 和 tool_calls 都为空但有 reasoning_content，
        # 说明 max_tokens 不足，所有 token 被 reasoning 消耗。
        # 逐级增加 max_tokens 重试，适配 reasoning 模型的 token 消耗。
        raw_tool_calls = msg.get('tool_calls', [])
        if not content and not raw_tool_calls and reasoning_content:
            req_tokens = data.get('max_tokens', 0)
            for attempt, increment in enumerate([8000, 16000], 1):
                retry_tokens = min(req_tokens + increment, 32000)
                if retry_tokens <= req_tokens:
                    break
                logger.warning(
                    f"[LLM] 检测到 reasoning 模型 token 耗尽 "
                    f"(reasoning={len(reasoning_content)} chars, content/tool_calls为空, "
                    f"req_max_tokens={req_tokens})，"
                    f"第 {attempt} 次重试 max_tokens={retry_tokens}"
                )
                retry_data = dict(data)
                retry_data['max_tokens'] = retry_tokens
                retry_response = requests.post(
                    url, headers=headers, json=retry_data,
                    timeout=effective_timeout
                )
                _log_and_raise(retry_response, call_id, t0)
                retry_result = retry_response.json()
                retry_choice = retry_result.get('choices', [{}])[0]
                retry_msg = retry_choice.get('message', {})
                content = retry_msg.get('content', '') or ''
                reasoning_content = retry_msg.get('reasoning_content', '') or None
                usage_data = retry_result.get('usage')
                if meta is not None:
                    meta.usage = usage_data or meta.usage
                    meta.finish_reason = (
                        retry_choice.get('finish_reason') or meta.finish_reason)
                raw_tool_calls = retry_msg.get('tool_calls', [])
                if content or raw_tool_calls:
                    break
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get('function', {})
            raw_args = fn.get('arguments', '{}')
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as e:
                # 尝试修复：补齐截断的 JSON
                if isinstance(raw_args, str):
                    fixed = _try_fix_json(raw_args)
                    if fixed:
                        parsed_args = fixed
                        logger.warning(f"LLM tool call 参数已自动修复: {tc.get('function', {}).get('name', '?')}")
                    else:
                        logger.warning(f"LLM 返回了无法修复的 tool call 参数: {e}，跳过")
                        continue
                else:
                    logger.warning(f"LLM 返回了无法解析的 tool call 参数: {e}，跳过")
                    continue
            tool_calls.append(ToolCall(
                name=fn.get('name', ''),
                arguments=parsed_args
            ))

        return content, reasoning_content, (tool_calls or None), usage_data

    def _request_anthropic_with_tools(self, messages: list, tools: list,
                                       max_tokens: Optional[int] = None,
                                       thinking: Optional[str] = None,
                                       tool_choice: str = 'auto',
                                       timeout: Optional[int] = None,
                                       endpoint: Optional[_Endpoint] = None,
                                       meta: Optional[_LLMMeta] = None):
        """Anthropic tool use 协议（meta 语义同 _request_anthropic）"""
        ep = endpoint or self._primary_endpoint()
        effective_timeout = timeout if timeout is not None else self.timeout
        headers = {
            'x-api-key': ep.api_key,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Anthropic: system 是顶层字段, tools 格式不同
        system_prompt = None
        api_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_prompt = m['content']
            else:
                api_messages.append(m)

        anthropic_tools = []
        for t in tools:
            fn = t.get('function', t)
            anthropic_tools.append({
                'name': fn.get('name', ''),
                'description': fn.get('description', ''),
                'input_schema': fn.get('parameters', {}),
            })

        data = {
            'model': ep.model,
            'messages': api_messages,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens,
            'tools': anthropic_tools,
        }
        # tool_choice 语义映射（OpenAI → Anthropic）：
        # required → {"type": "any"}（必须调用工具）；none → {"type": "none"}；
        # auto 为 Anthropic 默认行为，省略
        _tc_map = {'required': {'type': 'any'}, 'none': {'type': 'none'}}
        if tool_choice in _tc_map:
            data['tool_choice'] = _tc_map[tool_choice]
        # 思考模式开关（Anthropic 格式）：仅 enabled 时携带 reasoning 字段
        _thinking = thinking or self.thinking
        if _thinking == 'enabled':
            data['reasoning'] = {'effort': self.reasoning_effort}
        if system_prompt:
            data['system'] = system_prompt

        url = f'{ep.base_url}/messages'

        import uuid
        call_id = uuid.uuid4().hex[:8]
        t0 = time.time()
        _log_llm_request(call_id, ep.provider, ep.model, url, data)

        response = requests.post(url, headers=headers, json=data, timeout=effective_timeout)
        _log_and_raise(response, call_id, t0)
        result = response.json()

        _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)

        content = ""
        tool_calls = []
        usage_data = result.get('usage')

        for block in result.get('content', []):
            if block.get('type') == 'text':
                content += block.get('text', '')
            elif block.get('type') == 'tool_use':
                tool_calls.append(ToolCall(
                    name=block.get('name', ''),
                    arguments=block.get('input', {})
                ))

        if meta is not None:
            meta.usage = usage_data or meta.usage
            meta.finish_reason = result.get('stop_reason') or meta.finish_reason

        return content, None, (tool_calls or None), usage_data


# 全局客户端实例（延迟初始化）
_client: Optional[LLMClient] = None
_instances: Dict[str, LLMClient] = {}


def get_client(instance_id: str = "default") -> LLMClient:
    """获取或创建 LLM 客户端实例"""
    global _client
    if instance_id == "default":
        if _client is None:
            _client = LLMClient()
        return _client
    else:
        if instance_id not in _instances:
            _instances[instance_id] = LLMClient()
        return _instances[instance_id]


def clear_client_memory(instance_id: str = "default"):
    """清空指定实例的会话记忆"""
    client = get_client(instance_id)
    client.clear_memory()


# 兼容旧接口的快捷函数
def chat_with_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None
) -> str:
    """
    简单聊天接口（非流式）

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词
        max_tokens: 最大生成 token 数
        timeout: 超时时间（秒）

    Returns:
        LLM 响应文本
    """
    client = get_client()
    response = client.chat(prompt, system_prompt, use_memory=False, max_tokens=max_tokens, timeout=timeout)
    return response.content


def chat_with_llm_stream(
    prompt: str,
    system_prompt: Optional[str] = None
) -> Generator[str, None, None]:
    """
    流式聊天接口

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词

    Yields:
        文本片段
    """
    client = get_client()
    for chunk in client.chat_stream(prompt, system_prompt, use_memory=False):
        yield chunk
