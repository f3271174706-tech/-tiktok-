# 部署文档

## 服务器

- 配置：2核2G / 5M带宽 / 100G流量
- 系统：CentOS 8.4
- IP：103.236.98.149
- SSH 端口：58909

## SSH

```bash
ssh -p 58909 root@103.236.98.149
```

## 项目路径

```
/root/
├── main.py
├── downloader.py
├── requirements-server.txt
├── static/
│   └── index.html
├── downloads/
└── .cloudflared/
```

## 服务管理

| 服务 | 说明 |
|------|------|
| douyin-dl | FastAPI (端口 8000) |
| cloudflared | Cloudflare Tunnel |
| douyin-cleanup.timer | 每 30 分钟清理旧文件 |

```bash
systemctl restart douyin-dl
systemctl status douyin-dl
journalctl -u douyin-dl -n 20
```

## 部署代码

```bash
scp -P 58909 D:\mycode\douyin-downloader\static\index.html root@103.236.98.149:/root/static/
scp -P 58909 D:\mycode\douyin-downloader\main.py root@103.236.98.149:/root/
scp -P 58909 D:\mycode\douyin-downloader\downloader.py root@103.236.98.149:/root/
```

上传后重启 Python 文件才生效，改 HTML 不需要重启。

Python 文件上传后注意修复 Python 3.9 兼容：
```bash
sed -i 's/def _cache_get(key: str) -> dict | None:/def _cache_get(key: str):/' /root/downloader.py
sed -i 's/def extract_video_info(url: str) -> dict:/def extract_video_info(url: str):/' /root/downloader.py
systemctl restart douyin-dl
```

## ffmpeg

位于 `/usr/local/bin/ffmpeg`（静态二进制）。如丢失从本机重新上传。

## 域名

- fzpnowm.top（Spaceship，$1.34/年）
- DNS：Cloudflare
- Tunnel：fzp-downloader
- SSL：Cloudflare Flexible

## 注意事项

- 商家封了 80/443，HTTPS 走 Cloudflare Flexible SSL
- 服务器 Python 3.9，上传代码需修类型注解
