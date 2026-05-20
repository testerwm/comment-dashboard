# 评论爬取与可视化报告网站

本地站点地址默认是：

```bash
http://127.0.0.1:8787
```

## 启动

```bash
cd /Users/king/Documents/Codex/2026-05-20/comment-dashboard
python3 server.py
```

如果需要换端口：

```bash
PORT=8790 python3 server.py
```

## Docker

```bash
docker build -t comment-dashboard .
docker run --rm -p 8787:8787 \
  -e HOST=0.0.0.0 \
  -e PORT=8787 \
  -e DASHBOARD_PASSWORD=change-me \
  comment-dashboard
```

打开：

```bash
http://localhost:8787
```

浏览器会弹出基础认证，用户名可随便填，密码是 `DASHBOARD_PASSWORD`。

## 线上部署

如果采用“本地爬取 + 线上展示”，可以直接部署到 GitHub Pages。线上页面会自动进入静态展示模式：隐藏爬取功能，只保留本地 JSON 导入、表盘、报告和导出。

详细步骤见：

```text
DEPLOY.md
```

项目已经包含：

- `Dockerfile`
- `.dockerignore`
- `render.yaml`

可部署到支持 Docker Web Service 的平台，例如 Render、Railway、Fly.io 或自己的 VPS。

需要设置环境变量：

```bash
HOST=0.0.0.0
PORT=8787
DASHBOARD_PASSWORD=你的访问密码
```

注意：线上环境没有本机桌面浏览器。网站本身可以部署，Bilibili/小红书爬虫如果需要登录，建议后续接远程浏览器、持久化 profile volume，或只在本地完成登录态后再迁移 profile。

## 功能

- 支持 Bilibili 与小红书爬虫配置。
- 支持修改关键词、热门视频数量、帖子数量、每视频/每帖热门评论数、每条热门评论最多回复数、输出文件路径等变量。
- 高级筛选支持：
  - Bilibili：排序、视频时长。
  - 小红书：内容类型、排序。
- Bilibili 和小红书都支持从网站打开登录浏览器，登录完成后手动保存 profile。
- 爬取完成后自动加载 JSON 并打开可视化表盘。
- 支持导入已有 JSON 文件。
- 已内置识别两种结构：
  - `videos -> comments -> replies`
  - `posts -> comments/top_10_comments`
- 对其他平台的类似 JSON，会尝试从 `videos`、`posts`、`items`、`results`、`data` 中寻找列表，并归一化评论字段。

## 使用流程

1. 打开网站，选择平台。
2. 第一次使用先点对应平台的“打开登录窗口”按钮。
3. 在弹出的浏览器里完成登录。
4. 回到网站点击“保存登录状态”。
5. 设置关键词、数量和输出文件后点“开始爬取”。

登录窗口不会自动关闭，避免平台旧 cookie 被误判为登录成功。

## 注意

这个网站的后端不直接改你的原始爬虫脚本。Bilibili 使用站点内置的浏览器登录态爬虫；小红书会直接调用你现有脚本的命令行参数。

如果爬虫运行时报缺少依赖，请在对应环境安装：

```bash
pip install -r requirements-crawlers.txt
python -m playwright install chromium
```
