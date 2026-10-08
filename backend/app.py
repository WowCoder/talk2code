# -*- coding: utf-8 -*-
"""Talk2Code - Flask 主应用入口"""
from factory import app  # noqa: F401
import routes.auth        # noqa: F401 - register auth routes
import routes.requirements  # noqa: F401 - register requirement routes
import routes.health      # noqa: F401 - register health routes
import routes.preview     # noqa: F401 - register preview routes
import routes.publish     # noqa: F401 - register publish routes (Ship B)
import routes.published_site  # noqa: F401 - register published Host routing (Ship B)
import routes.market         # noqa: F401 - register 创意市集 routes
import routes.invite         # noqa: F401 - register 邀请码申请 routes
import routes.admin          # noqa: F401 - register 运营后台 routes
import routes.admin_traces   # noqa: F401 - register Agent 可观测性 routes
import routes.admin_evals    # noqa: F401 - register 评测观测 routes（只读）

if __name__ == '__main__':
    # PORT 可覆盖，便于在不占用主线 5001 端口的隔离实例上跑发布链路 e2e（B10）。
    # 默认仍是 5001，保持与历史启动方式一致。
    import os
    _port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=_port, debug=False, threaded=True)
