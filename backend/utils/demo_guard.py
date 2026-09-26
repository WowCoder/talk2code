# -*- coding: utf-8 -*-
"""演示模式写操作守卫（默认拒绝）。

演示模式 = 以演示帐号身份登录但被剥夺写权限（JWT 带 demo claim）。
守卫挂在 factory 的 before_request 链上：凡 POST/PUT/PATCH/DELETE 且
JWT 含 demo=true 的请求，不在白名单内一律 403。

为什么是「默认拒绝」而不是逐接口贴装饰器：贴装饰器是黑名单思维 ——
半年后新增一个写接口忘了贴，演示帐号就被写脏了，且没有任何告警。
默认拒绝把失败模式反转成「新接口默认被拦，需要显式评审放行」：
漏白名单的表现是**功能不可用**（立刻被发现），而不是**数据被改**
（可能几个月才发现）。
"""
from flask import request, jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 演示模式下仍然允许的写路径 —— 显式白名单，新增即需评审。
# 设计依据：docs/design/demo-invite-admin.md §2.2
DEMO_WRITE_ALLOWLIST = (
    "/api/login",            # 允许切换到真实账号
    "/api/logout",           # 必须能退出演示
    "/api/register",         # 演示 → 注册的转化路径
    "/api/invite/requests",  # 演示中也可以申请邀请码
    "/api/admin/",           # 后台由 admin_required 独立鉴权（header token，
                             # 且 header 优先级高于 cookie，守卫读到的就是 admin claim）
)


def is_demo_write_allowed(path: str) -> bool:
    return path.startswith(DEMO_WRITE_ALLOWLIST)


def demo_write_guard():
    """返回 403 响应（演示身份 + 写方法 + 非白名单），否则返回 None 放行。

    注意：本守卫不做鉴权。无 token / 坏 token 的请求原样放行，
    由各路由自己的 @jwt_required 处理。
    """
    if request.method not in WRITE_METHODS:
        return None
    if is_demo_write_allowed(request.path):
        return None
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:
        # 无 token 或 token 无效：optional=True 下 get_jwt() 可能无上下文。
        # 交给路由层处理 401/422，这里不掺和。
        return None
    if claims and claims.get("demo"):
        return jsonify({
            "error": "演示模式为只读，不可修改数据",
            "code": "demo_readonly",
        }), 403
    return None


def register_demo_guard(app) -> None:
    """把守卫挂到 before_request 链上。

    必须注册在 factory 的 _set_rate_limit_identity 之后：后者已经把 JWT
    上下文解析过一遍，本守卫的 verify_jwt_in_request 是廉价的二次确认。
    """

    @app.before_request
    def _demo_readonly_guard():
        return demo_write_guard()
