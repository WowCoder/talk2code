---
name: crud
description: 列表 + 表单 + 增删改查类需求：存储层分离、渲染与数据解耦、表单契约、空态
when_to_use: 需求是"能增删改查一批数据"的应用（待办清单、博客、记账、通讯录等）
level: L1
triggers: [待办, 清单, 记事, 博客, 记账, 通讯录, 购物车, 增删改查, todo]
---

# 增删改查（CRUD）应用规范

这类需求的成败不在界面好不好看，而在**数据能不能真的存下来、改完能不能立刻看到**。

## 文件骨架

```
index.html      结构 + 表单 + 列表容器
css/style.css   样式
js/storage.js   window.Store —— 唯一的持久化出入口（读写 JSON 都在这里）
js/app.js       window.App —— 状态、渲染、事件绑定
```

存储层必须独立成一个文件：**所有 localStorage 读写集中在 `js/storage.js`**，
其他文件只调 `Store.getAll() / Store.add() / Store.update() / Store.remove()`。
散落在各处的裸 `localStorage.setItem` 是数据不一致的主要来源。

## P0 存储层必须兜底

```js
(function (global) {
  var KEY = 'app.items';
  var memory = null;              // 存储不可用时的内存兜底

  function read() {
    if (memory) return memory;
    try {
      memory = JSON.parse(localStorage.getItem(KEY) || '[]');
    } catch (e) {
      memory = [];                // 沙箱禁用 localStorage / 数据损坏 → 降级
    }
    return memory;
  }

  function write(list) {
    memory = list;
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) {}
  }

  global.Store = { read: read, write: write };
})(window);
```

`JSON.parse` 也要包 try/catch——首次写入前或数据被外部改坏时会抛异常，
一抛整个初始化就断了，页面白屏。

## P0 表单 id 必须与 HTML 对账

JS 里 `getElementById('blogForm')` 的 id，**必须**在 index.html 中真实存在。
绑到不存在的元素不会报错，只会静默失效——表现为"点了保存没反应"，
是最难自查的一类缺陷。

写完逐条核对：每个 `getElementById` / `querySelector('#x')` 的 id 都能在
HTML 或 JS 动态创建的 id 里找到。

## P0 提交必须阻止默认行为

```js
Utils.on(form, 'submit', function (e) {
  e.preventDefault();            // 漏掉这行 → 表单原样刷新，数据全丢
  // ...
});
```

## P1 渲染由数据驱动

维护一份 `items` 数组，任何变更后调 `render()` 重画整个列表。
不要在某处手动 `appendChild` 一行、又在别处改 `innerHTML`——
两处逻辑迟早不同步，出现"数据变了但列表没变"。

渲染函数必须处理**空态**：

```js
if (!items.length) {
  listEl.innerHTML = '<li class="empty">还没有内容，添加一个吧</li>';
  return;
}
```

空态不能是空白——用户（和自动验收）会认为页面坏了。

## P1 编辑态复用表单

新增和编辑共用一个表单：编辑时把 id 存到表单的隐藏字段或 App 的 `editingId`，
提交后判断有 id 走 update、无 id 走 add。**提交后必须清空 `editingId`**，
否则下一次新增会误改成上次编辑的那条。

## P1 删除要有确认 + 立即重渲染

删除是不可逆操作，给一次 `confirm()`；删完立刻 `render()`，
不要只从 DOM 移除节点而不动数据（刷新后又回来了）。

## 自验收清单

- [ ] 新增一条 → 列表立刻出现，刷新页面后仍在
- [ ] 编辑一条 → 内容更新，且不会多出一条新的
- [ ] 删除一条 → 列表更新，刷新后不会复活
- [ ] 清空所有 → 显示空态文案，不是空白
- [ ] 控制台无报错，`localStorage` 相关访问全部包了 try/catch
