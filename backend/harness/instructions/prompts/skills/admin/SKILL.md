---
name: admin
description: 管理后台类需求：侧边栏 + 表格 + 弹窗三层结构、纯前端分页排序、弹窗状态重置
when_to_use: 需求是管理后台、运营平台、数据管理控制台
level: L1
triggers: [后台管理, 管理系统, 运营平台, 控制台, 数据管理, admin]
---

# 管理后台规范

## P0 先说清一件事：登录是模拟的

这里没有服务器，**做不出真实的用户登录与权限体系**。
如果需求里写了"登录"，按以下方式处理并在界面上说明：
- 做一个登录页/登录弹窗，用内置账号密码校验（纯前端比对），通过即进入主界面
- 或干脆跳过登录，直接进入主界面，右上角放一个"当前用户"下拉做身份切换的演示

**不要**做出看似登录成功、但刷新后状态丢失又无提示的实现。
也不要声称接了后端——做不到的事要讲清楚。

## 文件骨架

```
index.html      侧边栏 + 顶栏 + 主内容区 + 弹窗容器
css/style.css
js/data.js      window.MockData —— 内置示例数据集
js/table.js     window.Table —— 表格渲染、排序、分页（纯内存运算）
js/app.js       路由切换（哪个菜单激活）、事件绑定
```

## P0 菜单切换必须真的换内容

点击侧边栏菜单，主内容区必须渲染出对应板块。
高频缺陷：菜单高亮切换了，主区内容没变（只绑了 `active` 类，没绑渲染）。

用一个显式的状态字段驱动，而不是靠 DOM class 判断当前在哪：

```js
var currentView = 'dashboard';
function switchView(name) {
  currentView = name;
  renderNav();     // 更新高亮
  renderMain();    // 更新主区
}
```

## P0 表格操作是纯前端内存运算

排序、筛选、分页全部在内存数组上做，然后整体重渲染 `<tbody>`：

```js
function renderRows() {
  var list = MockData.users.slice();
  list = applyFilter(list);
  list = applySort(list);
  var page = paginate(list, pageSize, currentPage);
  tbody.innerHTML = page.map(rowHtml).join('');
  renderPager(list.length);
}
```

分页边界要处理：最后一页删完数据后 `currentPage` 要回退一页，
否则会停在空页上看着像"数据没了"。

## P0 弹窗必须重置状态

弹窗关闭（包括点遮罩、按 ESC）时：
- 清空表单字段
- 清空校验错误提示
- 清除 `editingId`

漏掉重置 → 上次编辑的内容残留在下次新增的表单里，保存就改错数据。

打开弹窗时焦点要落到第一个输入框，遮罩层不能默认 `hidden` 且不移除。

## P1 空态与加载态

- 表格无数据：显示一行"暂无数据"，跨列居中，不能是空 tbody
- 筛选无结果：同上，并给"清空筛选"按钮
- 删除操作：给一次 `confirm()`

## P1 数据规模要撑得起分页

内置示例数据至少 20-30 条，否则分页功能看不出效果（只有一页）。

## 自验收清单

- [ ] 点击每个侧边栏菜单 → 主区内容确实切换
- [ ] 表格排序、分页、筛选都能用，且结果正确
- [ ] 弹窗新增一条 → 表格出现该条；编辑 → 原地更新不多出一条
- [ ] 关闭弹窗再打开 → 表单是干净的
- [ ] 删除到空 → 显示空态，分页页码正确回退
- [ ] 无 `https://` 外链，控制台无报错
