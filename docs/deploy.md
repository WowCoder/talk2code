# Talk2Code 生产部署指南（腾讯云轻量应用服务器）

适用场景：把 Talk2Code 部署到一台腾讯云轻量应用服务器，对外提供访问（面试官演示 / GitHub 宣传）。
前置结论：**不需要 GPU**（LLM 走远程 API，BGE-M3 硬编码 CPU）。

---

## 一、本仓库实际确定的生产配置

| 项 | 值 |
|---|---|
| 机型 | 轻量 **4核8G** / 120GB SSD / 10M 带宽 / 1500GB 月流量 |
| 地域 | **广州**（大陆节点，需 ICP 备案） |
| 系统 | Ubuntu 22.04 LTS |
| 公网 IP | `129.204.47.217` |
| 域名 | `wowcoder.cn`（备案中 / 待申请） |
| 年成本 | **¥630**（腾讯云六周年活动价，已锁 1 年；2026-10-12 前同价续费/3 年更划算） |
| 备案 | 必须（域名解析到大陆服务器，未备案域名会被阻断访问） |

> 选型结论：同档「香港 通用型 ~320/月、锐驰型 280/月」年化约 ¥3360–3840，是广州的 **5 倍**。
> 演示/宣传场景可提前 1–2 周准备，故选广州 + 备案换取最低成本、最低延迟、可用 .cn 域名。

---

## 二、配置档位对照（为什么选广州 4核8G）

| 档位 | 地域/机型 | 年成本 | 备案 | 上线速度 | 说明 |
|---|---|---|---|---|---|
| **✅ 推荐** | 广州 4核8G | **¥630** | 需（1–2 周） | 慢 | 成本最优、国内延迟最低、可用 .cn |
| 备选 A | 香港 锐驰型 4核8G | ~¥3360 | 免 | 即时 | 不想等备案时的顶上方案，演示完可退 |
| 备选 B | 香港 通用型 4核8G | ~¥3840 | 免 | 即时 | 标准价，比锐驰型略贵 |
| 不推荐 | 锐驰型/无限流量 2核1G | 低 | 免 | 即时 | 内存太小，BGE-M3 单进程就吃 3GB，跑不动 |

> 2核4G 跑完整版会 OOM（BGE-M3 单进程常驻约 3GB）。若预算极紧只能上 2核4G，则不装
> `sentence-transformers`，代码自动切 TF-IDF 降级——功能完整但语义检索质量下降。本仓库已选 4核8G，无此问题。

---

## 三、ICP 备案流程（广州节点必须）

备案与域名后缀无关，**只取决于服务器是否在大陆**。广州节点必须备案；香港节点免备案。

1. **域名实名认证**：在腾讯云控制台完成 `wowcoder.cn` 的实名（个人/企业），通常几分钟–1 天。
2. **提交备案**：微信小程序搜「腾讯云备案」或访问 https://cloud.tencent.com/product/ba ，
   选择「网站备案」，填写服务器信息（IP `129.204.47.217`、地域广州、实例 ID 在轻量控制台查）。
3. **腾讯云初审**：1–2 个工作日，不合格会打回补充。
4. **管局审核**：广东省通信管理局，通常 1–2 周（短信核验 + 终审）。
5. **备案通过**：拿到备案号后，到 DNS 解析把 `wowcoder.cn` 的 A 记录指向 `129.204.47.217`，
   约 10 分钟内生效。

> ⚠️ 备案期间该域名**不可对外访问**（大陆会阻断未备案域名的 Web 请求）。
> 但备案通过前你可以用 **IP 直接访问做内部验证**：`http://129.204.47.217`（需先在 Nginx/防火墙放行 80）。
> 对外正式访问 = 备案通过 + 域名解析 + HTTPS（见步骤五）。

---

## 四、操作步骤

### 1. 服务器（已完成）
- 已购：广州 4核8G，公网 IP `129.204.47.217`，Ubuntu 22.04。

### 2. 域名 + 备案（进行中）
- 申请 `wowcoder.cn` 并完成实名 → 走第三节备案流程。
- 备案通过前，可用 `http://129.204.47.217` 做内部验证（见步骤 3–4，Nginx `server_name` 先写 IP 或 `_`）。

### 3. 上传部署文件并跑一键脚本
```bash
# 本地把 deploy/ 传到服务器
scp -r deploy root@129.204.47.217:/root/
ssh root@129.204.47.217
cd /root/deploy
# 脚本会：装依赖 → 克隆代码 → 建 venv → 装 Playwright → 预下载 BGE-M3 → 构建前端 → 起 PG/Redis → 起后端
# 备案通过前可用 IP 验证，先传 IP 占位；备案通过后改域名重跑 nginx 即可
sudo bash deploy.sh wowcoder.cn
```

### 4. 配置 Nginx（静态前端 + 反代后端 + SSE）
```bash
sudo cp /root/deploy/nginx.conf /etc/nginx/sites-enabled/talk2code
# 备案通过前：把 server_name 改成 IP 或 _ 做验证
sudo sed -i 's/wowcoder.cn/129.204.47.217/g' /etc/nginx/sites-enabled/talk2code
# 备案通过后：改回真实域名
# sudo sed -i 's/129.204.47.217/wowcoder.cn/g' /etc/nginx/sites-enabled/talk2code
sudo nginx -t && sudo systemctl restart nginx
```

### 5. 申请免费 SSL 证书（HTTPS 必备，否则浏览器标「不安全」）

备案和 SSL 是两回事：**备案不强制 SSL**，但对外服务强烈建议上 HTTPS（浏览器不标不安全、SSE/API 更稳、显专业）。证书免费：

```bash
# certbot 会自动修改 nginx.conf 追加 443 SSL 块并开启全站 HTTP→HTTPS 重定向
# 前提：域名 wowcoder.cn 已解析到本机且备案已通过（Let's Encrypt 需经 80 端口回源校验）
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d wowcoder.cn
# 续期是自动的；手动测试: certbot renew --dry-run
```

> 腾讯云也提供免费 DV 证书（控制台「SSL 证书」申请，DNS 验证），大陆节点可用；
> 但 certbot 最省心、自动续期，推荐。一键脚本 `deploy.sh` 末尾也会尝试自动申请（失败不中断）。

### 6. 验证
- 打开 `https://wowcoder.cn`（或备案前 `http://129.204.47.217`）→ 前端可加载。
- 发起一个需求 → SSE 实时输出、生成代码、预览可用。
- 看日志：`backend/logs/gunicorn.log`、`backend/logs/celery.log`。

---

## 五、生产必改项（脚本已写入 `.env`）

- `JWT_SECRET_KEY`：改为随机长串（`python -c "import secrets;print(secrets.token_hex(32))"`）。
- `DATABASE_URL`：必须指向 PostgreSQL（脚本已配 5434），**留空会回退 SQLite，pgvector 全失效**。
- `TRUST_PROXY_HEADERS=true`：否则限流按反代 IP 算，全员共享一个桶。
- `CORS_ORIGINS` / `PREVIEW_PUBLIC_BASE_URL`：改成真实域名（脚本已用 `wowcoder.cn` 替换）。
- gunicorn 用 **gthread**（`start.sh` 默认 `-w 2` 是 sync worker，SSE 长连接会占死 worker）。
- Nginx `proxy_buffering off` + 长超时（否则 SSE 流式输出被攒住）。
- `LLM_API_KEY`：在 `backend/.env` 填真实 Key（脚本只留占位）。

---

## 六、避坑

1. **续费翻倍**：上面全是新用户首年价，下单选同价续费/多年锁价（3 年 ¥2268 更划算）。
2. **备案阻塞**：广州节点未备案域名不可访问，提前 1–2 周启动备案；期间用 IP 内部验证。
3. **workspaces 无清理机制**：长期运行磁盘会无限增长，定期清理 `backend/workspaces`。
4. **BGE-M3 预下载**：脚本已做；若跳过，线上首次检索会卡 1–2 分钟去拉 1.9GB。
5. **别选「锐驰型/无限流量」小内存款**：2核1G 跑不动 Talk2Code（BGE-M3 单进程就 ~3GB）。
6. **日志保留**：APP 日志 90 天、单文件 50MB 轮转，已在 config 中；workspace 需自行清理。

---

## 七、宣传主页走 GitHub Pages（备案期间用域名访问）

备案拿到号之前，域名解析到大陆服务器会被拦截。把静态宣传页托管到 GitHub Pages，
`wowcoder.cn` 可以**立刻可用**，备案通过后再切回广州服务器（`deploy/nginx-landing.conf`）。

### 1. 发布（两种模式）

```bash
bash deploy/publish-gh-pages.sh                 # 模式 A: github.io 子路径（默认）
WITH_CNAME=1 bash deploy/publish-gh-pages.sh    # 模式 B: 额外绑定 wowcoder.cn
bash deploy/publish-gh-pages.sh --dry-run       # 只打包不提交不推送
```

| 模式 | 站点地址 | 前提 |
|---|---|---|
| **A（默认）** | `https://wowcoder.github.io/talk2code/` | 无。推完 1-2 分钟即可访问 |
| **B** | `https://wowcoder.cn` | 必须先配好 DNS，否则 github.io 会 301 到一个打不开的域名 |

> ⚠️ 带 CNAME 时 `wowcoder.github.io/talk2code` 会 **301 跳到自定义域名**，DNS 没配好则两处都打不开。
> 所以「先让 GitHub 用户看到」用模式 A，等 DNS 配好了再切模式 B。
> 自定义域名模板放在 `deploy/CNAME.template`，模式 B 时由脚本拷进分支根目录。

脚本用独立 worktree 建 `gh-pages` 孤儿分支（只含 `site/` 的文件），**不碰 main 工作区**。

### 2. GitHub 网页（首次一次）

Settings → Pages → Build and deployment → Source = **Deploy from a branch**，
Branch = **gh-pages**，Folder = **/(root)** → Save。

### 3. DNS（DNSPod）

| 类型 | 主机记录 | 记录值 |
|---|---|---|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| AAAA | `@` | `2606:50c0:8000::153` / `::8001` / `::8002` / `::8003`（可选） |
| CNAME | `www` | `WowCoder.github.io` |

验证：`dig wowcoder.cn +noall +answer` 应返回上面 4 个 IP。

### 4. 绑定域名 + HTTPS

Settings → Pages → Custom domain 填 `wowcoder.cn` → Save → 等证书签发（1 小时内）
→ 勾选 **Enforce HTTPS**。

### 5. 坑

- **CNAME 文件会让 `wowcoder.github.io/talk2code` 自动跳转到 `wowcoder.cn`**。
  所以加了 CNAME 就必须同时配好 DNS，否则两个地址都打不开；想先用 github.io 预览就临时删掉 CNAME。
- 大陆直连 GitHub Pages 速度不稳，仅作备案期间的过渡方案。
- 视频 1.9MB / 次访问，Pages 月带宽上限 100GB，正常宣传量级够用。
