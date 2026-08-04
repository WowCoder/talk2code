# -*- coding: utf-8 -*-
"""Talk2Code - Flask 主应用入口"""
from factory import app  # noqa: F401
import routes.auth        # noqa: F401 - register auth routes
import routes.requirements  # noqa: F401 - register requirement routes
import routes.health      # noqa: F401 - register health routes
import routes.preview     # noqa: F401 - register preview routes

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
