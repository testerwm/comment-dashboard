# 线上部署说明

## 最稳方案：本地爬取 + GitHub Pages 线上展示

适合：没有服务器，只想把分析表盘放到线上，爬虫仍然在本地运行。

这种模式下：

- 本地打开 `http://127.0.0.1:8787` 跑 Bilibili / 小红书爬虫。
- 生成 JSON 文件。
- 打开线上展示页。
- 用“本地导入”上传 JSON。
- 在线查看表盘、报告、原始 JSON，并导出归一化 JSON。

线上展示版不会连接后端，因此不会在线爬取，也不会读取服务器 `outputs/`。

### GitHub Pages 部署步骤

1. 在 GitHub 新建仓库，例如：

```text
comment-dashboard
```

2. 本地提交代码：

```bash
git init
git add .
git commit -m "initial comment dashboard"
git branch -M main
git remote add origin https://github.com/<你的用户名>/comment-dashboard.git
git push -u origin main
```

3. 打开 GitHub 仓库：

```text
Settings -> Pages
```

4. 选择：

```text
Source: Deploy from a branch
Branch: main
Folder: /root
```

5. 保存后等待 GitHub Pages 构建完成。

访问地址通常是：

```text
https://<你的用户名>.github.io/comment-dashboard/
```

### GitHub Pages 使用方式

1. 本地跑爬虫并生成 JSON。
2. 打开 GitHub Pages 链接。
3. 进入“导入”。
4. 选择本地 JSON 文件。
5. 查看表盘和报告。

注意：GitHub Pages 是静态托管，不支持 Python 后端、Playwright、登录浏览器、保存线上 JSON 文件。

这个项目可以作为 Docker Web 服务部署。推荐优先使用 VPS，因为项目包含 Playwright 爬虫、登录态 profile、JSON 输出文件，普通无状态平台不太适合完整爬虫功能。

## 推荐方案：VPS + Docker Compose

适合：网站在线访问、结果 JSON 持久保存、登录 profile 持久保存。

```bash
git clone <你的仓库地址> comment-dashboard
cd comment-dashboard
cp .env.example .env
```

编辑 `.env`：

```bash
PORT=8787
DASHBOARD_PASSWORD=改成强密码
```

启动：

```bash
docker compose up -d --build
```

访问：

```text
http://服务器IP:8787
```

数据会持久化在：

```text
./outputs
./profiles/bilibili
./profiles/xhs
```

## 重要限制

线上服务器通常没有可见桌面，因此“打开登录窗口”不会像本机一样弹到你的屏幕上。

可行方式：

- Bilibili：更适合线上部署。先在本地登录好，再把 `.bilibili-profile` 迁移到服务器的 `profiles/bilibili`。
- 小红书：当前是 GUI/Playwright 抓取，线上 headless 或无桌面环境更容易触发验证。完整线上登录需要额外做远程浏览器或 noVNC。
- Render/Railway：可以部署网站和导入/展示 JSON，但不推荐承担小红书 GUI 爬取。除非配置持久磁盘、远程浏览器和登录态迁移。

## Nginx / Caddy 反向代理

如果要绑定域名，建议用 HTTPS 反代到本服务的 8787 端口。

Caddy 示例：

```caddy
your-domain.com {
  reverse_proxy 127.0.0.1:8787
}
```

## 更新

```bash
git pull
docker compose up -d --build
```
