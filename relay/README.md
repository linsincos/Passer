# Aira 公网中继

这是 Aira Mobile 与 Passer 的无状态密文中继。手机和电脑都只需访问公网
HTTPS，不需要公网 IP、端口映射或处于同一网络。

中继无法解密 `aira-passer-v2` 的动作、参数和结果；它只接收由电脑 ID 与
256 位远程令牌派生的匿名路由凭据，原始远程令牌不会发送给中继。该令牌还
参与端到端认证密钥派生。服务重启后不保留会话、令牌或指令。

本机测试：

```powershell
python relay\aira_relay_server.py --host 127.0.0.1 --port 8787
```

生产环境应只监听 `127.0.0.1`，再由 Caddy/Nginx 提供 HTTPS。Caddy 示例：

```caddy
relay.example.com {
    reverse_proxy 127.0.0.1:8787
    request_body {
        max_size 80KB
    }
}
```

也可以在 `relay/` 中构建独立容器：

```bash
docker build -t aira-relay relay
docker run -d --restart unless-stopped --name aira-relay \
  -p 127.0.0.1:8787:8787 aira-relay
```

然后在 Passer 的“手机 Aira → 远程设置”中填写
`https://relay.example.com`。首次仍在同一局域网执行一次手机“连接测试”，
加密获取远程地址和随机令牌；此后离开该网络会自动改走公网中继。
