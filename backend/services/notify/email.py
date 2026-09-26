# -*- coding: utf-8 -*-
"""邀请码邮件发送（SMTP）。

核心约束：**发送失败绝不向上抛**。审批与发信解耦 —— 审批永远成功落库，
发信失败只把 delivery_status 记为 failed，后台可「重新发送」。把外部依赖
（SMTP）从审批事务里摘出去，是失败隔离，不是偷懒。

SMTP_ENABLED=false 时返回 skipped（本地开发 / 无 SMTP 环境），此时后台
详情页直接展示码明文供管理员手动复制，审批流程照样闭环。
"""
import smtplib
import ssl
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

from config import settings
from harness.observability.logger import get_logger

logger = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 10


def registration_hint() -> str:
    """邮件正文里的注册入口提示。"""
    return "注册时在「邀请码」一栏填入即可（每个邀请码只能注册一个账号）。"


def send_invite_code(to_email: str, code: str, hint: str = "") -> str:
    """发送邀请码邮件。返回 'sent' / 'skipped' / 'failed'，永不抛异常。"""
    subject = f"Talk2Code 邀请码：{code}"
    body = (
        "你好！\n\n"
        "你申请的 Talk2Code 邀请码已通过审批：\n\n"
        f"    {code}\n\n"
        f"有效期 {settings.INVITE_CODE_TTL_DAYS} 天，请尽快使用。\n"
        f"{hint or registration_hint()}\n\n"
        "如果这不是你本人的操作，请忽略本邮件。\n\n"
        "—— Talk2Code"
    )
    return _send(to_email, subject, body)


def send_rejection(to_email: str, reason: str = "") -> str:
    """发送拒绝通知。返回值同上，永不抛异常。"""
    subject = "Talk2Code 邀请码申请结果通知"
    body = (
        "你好！\n\n"
        "感谢你对 Talk2Code 的关注。很遗憾，你提交的邀请码申请本次未通过。\n"
        + (f"备注：{reason}\n" if reason else "")
        + "\n欢迎完善申请信息后再次提交。\n\n"
        "—— Talk2Code"
    )
    return _send(to_email, subject, body)


def _send(to_email: str, subject: str, body: str) -> str:
    if not settings.SMTP_ENABLED:
        return "skipped"
    if not (settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD):
        logger.warning("SMTP_ENABLED=true 但 SMTP 配置不完整，发信按失败处理")
        return "failed"

    try:
        msg = _build_message(to_email, subject, body)
        if settings.SMTP_PORT == 465:
            # 465 端口 SSL 直连（QQ/163 等邮箱的隐式 TLS 端口）
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                  timeout=_SMTP_TIMEOUT_SECONDS, context=context) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [to_email], msg.as_string())
        else:
            # 25/587 明文连接，按 SMTP_USE_TLS 决定是否升级 STARTTLS
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=_SMTP_TIMEOUT_SECONDS) as server:
                if settings.SMTP_USE_TLS:
                    server.starttls(context=ssl.create_default_context())
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [to_email], msg.as_string())
        logger.info(f"邮件已发送: {to_email}（{subject}）")
        return "sent"
    except Exception as e:
        # 完整异常只进日志（可能含账号信息），调用方只拿到 failed 状态
        logger.warning(f"邮件发送失败: {to_email}（{subject}）: {e}")
        return "failed"


def _build_message(to_email: str, subject: str, body: str) -> MIMEText:
    from_email = settings.SMTP_FROM or settings.SMTP_USER
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("Talk2Code", "utf-8")), from_email))
    msg["To"] = to_email
    return msg
