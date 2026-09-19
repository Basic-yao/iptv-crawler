# 我的 IPTV 列表生成器

用自己的**频道清单**驱动，从公开源池抓取直播地址 → 按关键词匹配 → 验证可播 → 测速排序 → 定时写回仓库。

> ⚠️ 仅用于抓取**公开免费**的直播源；请遵守目标站点规则，勿用于付费/鉴权源破解、勿暴力探测。

## 目录结构

```
channels.txt    # 👈 你维护这里：想要的频道（输出名,关键词...）
sources.txt     # 👈 候选源地址池（每行一个公开 m3u/txt 的 http(s) 地址）
crawl_iptv.py   # 核心脚本：抓取+匹配+验证+排序
requirements.txt
.github/workflows/update_iptv.yml  # 定时运行并 commit 结果
```

运行后自动生成：
- `my_list.m3u` — 标准 m3u，播放器通用
- `hd.txt` — 兼容 IPTV 仓库的 `频道名,url` 格式
- `report.txt` — 本次抓取/匹配/可用统计

## 快速开始（本地）

```bash
pip install -r requirements.txt
python crawl_iptv.py
```

## 如何添加自己的源

### 方式 1：扩展"源池"（推荐）
在 `sources.txt` 追加你信任的公开源地址（每行一个 `.m3u` 或 `.txt`）。脚本会自动抓取、解析、纳入匹配。

### 方式 2：直接注入固定地址
若某个地址确定可用、想强制包含，可在 `crawl_iptv.py` 的 `main()` 里解析完成后手动：
```python
selected.setdefault("CCTV-1", set()).add("http://你的地址")
```

### 编辑频道清单
修改 `channels.txt`，格式：
```
输出频道名,关键词1,关键词2,...
```
候选源里**包含任一关键词**的频道即被匹配（不区分大小写）。

## 关键参数（脚本顶部）
- `KEEP_PER_CHANNEL`：每频道保留前 N 个（按响应速度）
- `CONCURRENCY` / `CHECK_TIMEOUT`：并发与超时

## 部署到 GitHub（全新仓库）

1. GitHub 上 **New repository**，命名如 `my-iptv`，选 **Public**，**不要**勾选初始化 README
2. 本地：
   ```bash
   git init
   git add .
   git commit -m "init: iptv crawler"
   git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git
   git branch -M master
   git push -u origin master
   ```
3. 进仓库 **Actions** 选项卡，启用工作流，可手动 Run workflow 测试
4. 定时每 6 小时自动跑，结果 commit 回仓库

## 分发地址
- m3u：`https://raw.githubusercontent.com/YOUR_USERNAME/REPO_NAME/master/my_list.m3u`
- hd.txt：`https://raw.githubusercontent.com/YOUR_USERNAME/REPO_NAME/master/hd.txt`

## 注意：反爬问题
部分公开源会屏蔽 GitHub Actions 机房 IP（返回 403），导致抓不到数据。若遇到此情况：
- 把脚本放到**自己的服务器 / 软路由 / 国内机器**跑（出口 IP 不被屏蔽），仓库只做托管
- 或在 `sources.txt` 换成**不反爬、对机器可达**的源地址

> 提示：公开仓库 Actions 定时可能不准点，可随时在 Actions 页面手动触发。
