---
name: tool
description: 输入→处理→输出的工具类需求：边界输入、输出区占位、复制降级、即时反馈
when_to_use: 需求是"输入一个东西、得到另一个东西"的工具（计算器、转换器、格式化、编解码）
level: L1
triggers: [计算器, 转换器, 换算, 格式化, 加密, 解密, 编码, 解码, 校验]
---

# 工具类应用规范

工具类的验收标准是**随便输什么都不会崩，且结果立即可见**。

## 文件骨架

```
index.html      输入区 + 操作区 + 输出区（三段式）
css/style.css
js/core.js      window.Core —— 纯计算逻辑，不碰 DOM
js/app.js       绑定事件、调 Core、写回输出区
```

计算逻辑与 DOM 解耦：`js/core.js` 只做「输入 → 输出」的纯函数，
这样边界用例（空串、非法字符、超大数）能被集中处理，也便于自查。

## P0 输出区必须有初始内容

结果区不能初始为空——页面刚打开时用户（和自动验收）看到一片空白，
会判定"这个工具坏了"。给一句占位文案：

```html
<output id="result" class="result">结果会显示在这里</output>
```

首次计算后替换成真实结果；输入被清空时**回到占位文案**，不要留着上一次的结果。

## P0 边界输入必须给反馈，不能静默

每个转换函数都要显式处理这些情况，并在界面上给出可读提示：

| 输入 | 期望行为 |
|---|---|
| 空字符串 | 显示占位文案，不报错 |
| 非数字 / 非法格式 | 提示"请输入有效的数字"，而不是显示 `NaN` |
| 超长文本 | 正常处理或给出长度提示，不卡死 |
| 除零 / 溢出 | 显示 `∞` 或"无法计算"，不抛异常 |

```js
function convert(text) {
  if (!text || !text.trim()) return { ok: false, message: '请输入内容' };
  var n = Number(text);
  if (!isFinite(n)) return { ok: false, message: '请输入有效的数字' };
  return { ok: true, value: n * 2.54 };
}
```

## P0 复制功能必须降级

`navigator.clipboard` 在 `file://` 下经常不可用（非安全上下文）。
直接调用会抛异常，点了"复制"毫无反应。必须降级：

```js
function copy(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {}
  try {
    var ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta);
    ta.select();
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}
```

两种都失败时，提示"请手动选中复制"，并且**输出区必须是可选中的文本**
（用 `<output>` / `<textarea>` / `<pre>`，不要用不可选中的 div）。

## P1 触发时机要明确

实时计算（边输边算）和按钮触发（点"转换"才算）二选一，不要混着来。
- 纯字符串处理（编码、格式化）→ 实时
- 耗时或需要多字段填完的（批量转换、加密）→ 按钮触发

用实时计算时加防抖（150-300ms），避免每敲一个字符都跑一遍。

## P1 提供反向与示例

- 单向工具：给出 2-3 个示例值，用户点一下就能填进去试
- 双向工具（如摄氏↔华氏）：提供"交换"按钮，而不是让用户手动改选择框

## 自验收清单

- [ ] 页面打开时输出区有占位文案，不是空白
- [ ] 输入空值 / 非法值 → 有可读提示，控制台无报错
- [ ] 输入正常值 → 结果正确且立即显示
- [ ] 复制按钮点了有反馈（成功提示或手动复制提示）
- [ ] 清空输入 → 输出回到占位态
