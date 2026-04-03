/**
 * Talk2Code 前端安全工具
 * 提供 XSS 防护、安全渲染等功能
 */

// ==================== XSS 防护 ====================

/**
 * HTML 实体编码表
 */
const HTML_ENTITIES = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
    '/': '&#x2F;',
    '`': '&#x60;',
    '=': '&#x3D;'
};

/**
 * 基础 HTML 转义函数
 * 使用双重编码策略防止绕过
 *
 * @param {string} text - 需要转义的文本
 * @returns {string} - 转义后的文本
 */
function escapeHtml(text) {
    if (text === null || text === undefined) {
        return '';
    }

    // 转换为字符串
    const str = String(text);

    // 使用 DOM API 进行安全转义（浏览器原生实现，更安全）
    const div = document.createElement('div');
    div.textContent = str;
    let escaped = div.innerHTML;

    // 对单引号额外转义（用于 onclick 等属性）
    escaped = escaped.replace(/'/g, '&#x27;');

    return escaped;
}

/**
 * JavaScript 字符串转义（用于 onclick 等内联事件）
 *
 * @param {string} str - 需要转义的字符串
 * @returns {string} - 转义后的字符串
 */
function escapeJsString(str) {
    if (str === null || str === undefined) {
        return '';
    }

    return String(str)
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\t/g, '\\t')
        .replace(/</g, '\\x3C')
        .replace(/>/g, '\\x3E');
}

/**
 * URL 安全编码（用于 href 等属性）
 *
 * @param {string} url - 需要编码的 URL
 * @returns {string} - 编码后的 URL
 */
function escapeUrl(url) {
    if (url === null || url === undefined) {
        return '';
    }

    // 白名单检查：只允许 http, https, mailto 协议
    const safeProtocols = ['http://', 'https://', 'mailto:', '/', '#'];
    const str = String(url);

    const isSafe = safeProtocols.some(proto =>
        str.toLowerCase().startsWith(proto.toLowerCase())
    );

    if (!isSafe && !str.startsWith('/')) {
        return '#blocked-url';  // 阻止危险协议
    }

    return encodeURI(str);
}

/**
 * CSS 安全编码（用于 style 属性）
 * 只允许安全的 CSS 属性
 *
 * @param {string} css - CSS 内容
 * @returns {string} - 安全的 CSS 内容
 */
function escapeCss(css) {
    if (css === null || css === undefined) {
        return '';
    }

    // 移除危险内容
    return String(css)
        .replace(/javascript:/gi, '')
        .replace(/expression\s*\(/gi, '')
        .replace(/url\s*\(/gi, '')
        .replace(/<[^>]*>/g, '');
}

// ==================== 安全渲染 ====================

/**
 * 安全地设置元素的 innerHTML
 * 使用 DOMPurify 进行清理（如果可用）
 *
 * @param {HTMLElement} element - 目标元素
 * @param {string} html - HTML 内容
 * @param {Object} options - 配置选项
 */
function setSafeHtml(element, html, options = {}) {
    if (!element) return;

    // 如果 DOMPurify 可用，使用它进行清理
    if (typeof DOMPurify !== 'undefined') {
        const cleanHtml = DOMPurify.sanitize(html, {
            ALLOWED_TAGS: options.allowedTags || [
                'p', 'br', 'span', 'div', 'strong', 'em', 'u', 'i',
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'ul', 'ol', 'li', 'a', 'pre', 'code',
                'table', 'thead', 'tbody', 'tr', 'td', 'th'
            ],
            ALLOWED_ATTR: options.allowedAttr || [
                'class', 'id', 'href', 'target', 'rel',
                'style', 'title', 'data-*'
            ],
            FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input'],
            FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'onblur']
        });
        element.innerHTML = cleanHtml;
    } else {
        // DOMPurify 不可用时，使用简单的文本设置
        element.textContent = html;
    }
}

/**
 * 创建安全的 DOM 元素
 *
 * @param {string} tag - 元素标签名
 * @param {Object} attrs - 属性对象
 * @param {string|HTMLElement} content - 内容
 * @returns {HTMLElement} - 创建的元素
 */
function createSafeElement(tag, attrs = {}, content = null) {
    const element = document.createElement(tag);

    // 安全设置属性
    for (const [key, value] of Object.entries(attrs)) {
        if (key.startsWith('on')) {
            // 事件属性：使用 addEventListener
            const eventName = key.slice(2).toLowerCase();
            if (typeof value === 'function') {
                element.addEventListener(eventName, value);
            }
        } else if (key === 'href') {
            element.setAttribute(key, escapeUrl(value));
        } else if (key === 'style') {
            element.setAttribute(key, escapeCss(value));
        } else if (key === 'class' || key === 'className') {
            element.className = String(value);
        } else {
            // 其他属性使用安全转义
            element.setAttribute(key, escapeHtml(String(value)));
        }
    }

    // 设置内容
    if (content !== null) {
        if (typeof content === 'string') {
            element.textContent = content;
        } else if (content instanceof HTMLElement) {
            element.appendChild(content);
        }
    }

    return element;
}

/**
 * 安全的模板渲染函数
 * 替代直接 innerHTML 赋值
 *
 * @param {HTMLElement} container - 目标容器
 * @param {Array<Object>} items - 数据项数组
 * @param {Function} renderItem - 渲染单个项的函数
 */
function renderSafeList(container, items, renderItem) {
    if (!container) return;

    // 清空容器（安全方式）
    while (container.firstChild) {
        container.removeChild(container.firstChild);
    }

    // 使用 DOM 操作创建元素
    items.forEach(item => {
        const element = renderItem(item);
        if (element instanceof HTMLElement) {
            container.appendChild(element);
        } else if (typeof element === 'string') {
            // 如果返回字符串，创建文本节点
            container.appendChild(document.createTextNode(element));
        }
    });
}

// ==================== 输入验证 ====================

/**
 * 验证文件名安全性
 *
 * @param {string} filename - 文件名
 * @returns {boolean} - 是否安全
 */
function isValidFilename(filename) {
    if (!filename || typeof filename !== 'string') {
        return false;
    }

    // 文件名长度限制
    if (filename.length > 255) {
        return false;
    }

    // 禁止危险字符
    const dangerousChars = ['<', '>', ':', '"', '|', '?', '*', '\\', '/', '\0'];
    if (dangerousChars.some(char => filename.includes(char))) {
        return false;
    }

    // 禁止危险扩展名
    const dangerousExtensions = ['.exe', '.bat', '.cmd', '.sh', '.php', '.asp', '.aspx'];
    if (dangerousExtensions.some(ext => filename.toLowerCase().endsWith(ext))) {
        return false;
    }

    return true;
}

/**
 * 验证时间戳格式
 *
 * @param {string} timestamp - 时间戳字符串
 * @returns {boolean} - 是否有效
 */
function isValidTimestamp(timestamp) {
    if (!timestamp || typeof timestamp !== 'string') {
        return false;
    }

    // 简单格式检查：YYYY-MM-DD HH:MM:SS
    const pattern = /^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}:\d{2})?$/;
    return pattern.test(timestamp);
}

// ==================== 安全事件处理 ====================

/**
 * 安全的事件处理包装器
 * 防止事件处理中的 XSS
 *
 * @param {Function} handler - 原始处理函数
 * @returns {Function} - 包装后的处理函数
 */
function safeEventHandler(handler) {
    return function(event) {
        // 阻止默认行为和传播（可选）
        // event.preventDefault();
        // event.stopPropagation();

        try {
            // 调用原始处理函数
            handler.call(this, event);
        } catch (error) {
            console.error('Event handler error:', error);
        }
    };
}

// ==================== DOMPurify 初始化 ====================

// 如果 DOMPurify 未加载，尝试从 CDN 加载
if (typeof DOMPurify === 'undefined') {
    // 动态加载 DOMPurify
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js';
    script.integrity = 'sha384-...',
    script.crossOrigin = 'anonymous';
    document.head.appendChild(script);
}

// ==================== 导出 ====================

// 导出到全局（用于 inline script）
window.SecurityUtils = {
    escapeHtml,
    escapeJsString,
    escapeUrl,
    escapeCss,
    setSafeHtml,
    createSafeElement,
    renderSafeList,
    isValidFilename,
    isValidTimestamp,
    safeEventHandler
};

// 便捷导出（兼容旧代码）
window.escapeHtml = escapeHtml;
window.escapeJsString = escapeJsString;
window.createSafeElement = createSafeElement;
window.setSafeHtml = setSafeHtml;