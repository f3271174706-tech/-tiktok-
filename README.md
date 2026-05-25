# 抖音/TikTok 无水印下载器

在线粘贴抖音或 TikTok 分享链接，即可无水印下载视频、提取音频、保存图文图片。

## 在线地址

https://fzpnowm.top

## 功能

**视频**
- 无水印 MP4 下载，可选 720p / 1080p / HD 画质
- MP3 音频提取
- 支持抖音短链（`v.douyin.com`）和 TikTok 链接

**图文帖**
- 逐张下载原图（WebP/JPEG/PNG/GIF）
- 自动识别普通图集和幻灯片（slides）
- 幻灯片有背景音乐时，一键合成 MP4 视频（图片 + 音频）

**网页界面**
- 粘贴分享文本自动提取链接
- 视频直链预览播放
- 图片画廊 + 翻页浏览
- 响应式布局，桌面端和手机端均可使用

## API

第三方应用可通过 GET 请求调用：

| 接口 | 说明 |
|------|------|
| `GET /api/info?url=<链接>` | 返回视频/图文信息 |
| `GET /api/video?url=<链接>&quality=1080p` | 301 跳转到无水印视频 |
| `GET /api/image_redirect?url=<链接>&index=0` | 301 跳转到单张图片 |
| `GET /api` | 接口文档 |

## 技术栈

| 层 | 方案 |
|---|------|
| 后端 | Python FastAPI |
| 下载引擎 | yt-dlp + 直接 HTTP 爬取 |
| 前端 | HTML + Tailwind CSS（亮色简约风） |
| 视频合成 | ffmpeg（图片 + 音频 → MP4） |
| 部署 | CentOS + Cloudflare Tunnel |
| 域名 | fzpnowm.top，HTTPS，24h |

## 源码

https://github.com/f3271174706-tech/-tiktok-

## 本地开发

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://localhost:8000`。

内网穿透：

```bash
ngrok http 8000
# 或
cloudflared tunnel --url http://localhost:8000
```

## 部署

详见 [DEPLOY.md](DEPLOY.md)

## 依赖

- Python 3.10+
- FastAPI + uvicorn + httpx
- yt-dlp + curl-cffi
- ffmpeg

## 成本

| 项目 | 价格 |
|------|------|
| 域名 fzpnowm.top | ~7 元/年 |
| 云服务器 2核2G 5M | 68/年 |
| Cloudflare Tunnel | 免费 |
| 合计 | ~75 元/年 |
