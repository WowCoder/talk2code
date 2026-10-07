将以下验收条件翻译为 Playwright DOM 操作序列。

<!-- 本模板由 Python str.format 渲染：正文里出现的 JSON 示例必须写成 `{{ }}`
     双花括号转义，否则 load_prompt_template 会抛 KeyError，把整批 AC 翻译打挂。 -->

<!-- 这里只放**每批/每个需求不同**的内容；恒定规则全在
     verify/ac_translator_system.md（system 消息）里。

     顺序是有意为之：AC 翻译按批连续调用多次（每批 2 条），把跨批恒定的
     渲染方式 / 可见文案 / 选择器清单排在**前面**，把每批都变的 anchor 与
     AC 文本排在**最后** —— 这样同需求的多次调用能共享最长公共前缀，
     prefix cache 才吃得满（此前 anchor 在最前，4 次调用公共前缀只有
     197 字符，缓存几乎完全不命中）。 -->

## 渲染方式（harness 检测实际代码得出的事实，不要自行猜测）
{render_info}

## 页面上真实可见的文案
{visible_text_text}

## 可用 CSS 选择器
{selector_text}

## 每条 AC 的定位依据（anchor）
{anchor_text}

## 需要翻译的验收条件
{ac_text}
