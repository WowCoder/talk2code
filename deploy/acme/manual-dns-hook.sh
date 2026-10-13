#!/usr/bin/env bash
# certbot 手动 DNS-01 的 auth hook —— 供「远程 / 分步」签发泛域名证书使用。
#
# 为什么需要它：`certbot --manual` 默认在终端里提示"请添加 TXT 记录后回车"，
# 而远程操作（先起签发 → 拿到 TXT 值 → 去 DNS 控制台添加 → 再回来放行）没法在
# 同一个交互式会话里完成。本 hook 把 ACME 下发的值写进文件并阻塞等待信号文件，
# 从而把一次签发拆成两步执行。交互式会话直接用 `certbot --manual` 即可，不需要它。
#
# 用法（在服务器上）：
#   install -m 755 manual-dns-hook.sh /tmp/acme-auth.sh
#   rm -f /tmp/acme_txt_pending.txt /tmp/acme_dns_ready
#   setsid nohup certbot certonly --manual --preferred-challenges dns \
#     --cert-name wildcard.<APEX> -d '*.<APEX>' \
#     --manual-auth-hook /tmp/acme-auth.sh --manual-cleanup-hook /bin/true \
#     --non-interactive --agree-tos > /tmp/acme-issue.log 2>&1 &
#   # 1) 读 /tmp/acme_txt_pending.txt → 得到 "_acme-challenge.<APEX> 的值"
#   # 2) 到 DNS 控制台添加该 TXT 记录
#   # 3) touch /tmp/acme_dns_ready 放行
#
# ⚠️ 超时（默认 30 分钟）即失败退出：手动签发的证书本来就不会自动续期，这里是
#    兜底防线 —— 防止无人值守的 `certbot renew` 跑到这个 hook 后无限挂住。
set -u

printf '%s %s\n' "${CERTBOT_DOMAIN:-?}" "${CERTBOT_VALIDATION:-?}" >> /tmp/acme_txt_pending.txt
echo "[auth-hook] 已登记 ${CERTBOT_DOMAIN} 的 TXT 值 → 等待 /tmp/acme_dns_ready"

for _ in $(seq 1 900); do
  [ -f /tmp/acme_dns_ready ] && break
  sleep 2
done

if [ ! -f /tmp/acme_dns_ready ]; then
  echo "[auth-hook] 等待超时（30 分钟），放弃"
  exit 1
fi

echo "[auth-hook] 收到放行信号，再等 25s 让 DNS 传播"
sleep 25
exit 0
