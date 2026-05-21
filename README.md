# 评论爬取与可视化报告网站

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


这个网站的后端不直接改你的原始爬虫脚本。Bilibili 使用站点内置的浏览器登录态爬虫；小红书会直接调用你现有脚本的命令行参数。

如果爬虫运行时报缺少依赖，请在对应环境安装：

```bash

# 复制粘贴到命令窗口 去依次执行
python3 -m venv .venv
source .venv/bin/activate

# 这三行依次点击运行即可
python -m pip install --upgrade pip
python -m pip install -r requirements-crawlers.txt
python -m playwright install chromium

# 执行完成上面的命令后，命令窗口复制粘贴 回车运行该命令
python3 server.py
```

本地站点地址默认是：

```bash
http://127.0.0.1:8787
```

## 启动

```bash
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

