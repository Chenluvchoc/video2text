# 链接转文字工作台

粘贴短视频分享链接 → 自动提取文案。**本地转写，无时长限制，不看广告，不花钱。**

## 它解决什么问题

| 市面上的小程序 | 本工作台 |
|---|---|
| 限 5 分钟以内的视频 | 任意时长（1小时也能转） |
| 要看 30 秒广告解锁 | 无广告，本地跑 |
| 按次收费 / 限次数 | 无限次 |
| 服务器随时跑路 | 代码在你手里 |
| 转写质量取决于它接的 API | whisper-medium 本地模型，可自行升级 large-v3 |

## 支持平台

- **抖音**（分享短链 / 视频页链接均可，粘贴整段分享文本自动识别）
- **B 站**（视频页 / b23.tv 短链）
- **快手 / 小红书 / 微博视频 / 西瓜视频等**（走 yt-dlp 通用解析，覆盖 1000+ 站点，具体成功率取决于平台风控）

## 环境要求

- Python 3.9+（3.10/3.11 最佳）
- ffmpeg（必须）
- 磁盘：模型文件约 1.5GB（首次运行自动从国内源下载）
- 内存：4GB+（转写时 medium 模型约占 2GB）

### 安装 ffmpeg

- **Windows**：`winget install ffmpeg` 或从 https://www.gyan.dev/ffmpeg/builds/ 下载后把 bin 目录加入 PATH
- **macOS**：`brew install ffmpeg`
- **Ubuntu/Debian**：`sudo apt install ffmpeg`

## 一键启动

### Windows
双击 `run.bat`（首次运行自动装依赖+模型，之后秒启动）

### macOS / Linux
```bash
chmod +x run.sh && ./run.sh
```

启动后浏览器打开 **http://localhost:8300**

### 手机使用（同一 WiFi）
1. 电脑上查本机 IP：
   - Windows：`ipconfig` 看 IPv4 地址
   - macOS：`ifconfig | grep inet`
   - Linux：`hostname -I`
2. 手机浏览器访问 `http://电脑IP:8300`
3. 加到主屏幕，效果接近 App

## 使用方法

**单链接**（日常最常用）

1. 抖音里点「分享 → 复制链接」
2. 打开工作台，点「📋 粘贴」（或手动粘贴）
3. 点「提取文字」→ 等进度条跑完
4. 结果页：一键复制全文 / 带时间戳复制 / 下载 txt / 下载 SRT 字幕；历史记录自动保存

**B 站收藏夹批量**

1. B 站 App「我的 → 收藏」打开收藏夹，右上角分享 → 复制链接
2. 切到「⭐ B站收藏夹」页粘贴，选条数，点开始
3. 每条视频串行转写，点任意一条可查看完整文案、下载该条的 SRT

**Word 文案库**（领导发来一文档链接，一键全转）

1. 切到「📄 Word 文案库」页，上传领导给的 .docx
2. 自动识别文档里所有视频链接——正文粘贴的链接文本、表格里的链接、超链接按钮都支持
3. 逐条转写（全平台：抖音/B站/快手/小红书…），完成后点「📥 下载 Word 文案库」
4. 成品 Word 含每条视频的标题、作者、时长、来源链接和带时间戳的完整文案，可直接提交

## 配置（环境变量，可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WHISPER_MODEL` | `medium` | 转写模型：`small`（快、略糙）/ `medium`（推荐）/ `large-v3`（最准、最慢、需 4GB+ 内存） |
| `PORT` | `8300` | 服务端口 |
| `KEEP_HISTORY` | `1` | 是否保留历史记录，`0` 关闭 |

例如用最准的模型：`WHISPER_MODEL=large-v3 python app.py`

## 工作原理

```
分享文本 ──正则──> URL ──> 平台分流
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
        抖音/快手/小红书                    B 站
        yt-dlp + 自动刷新 cookies           官方 API 直连
              │                                │
              └───────────┬────────────────────┘
                          ▼
                下载最低画质（视频不长期落盘）
                          ▼
              ffmpeg 抽取 16kHz 单声道音轨
                          ▼
        faster-whisper 本地转写（CPU, int8）
                          ▼
            分段文本 + 时间戳 → 前端展示
```

**关键技术点（为什么小程序总失效而它不会）：**
- 抖音网页接口需要"新鲜 cookies"，本工具用无头浏览器自动获取并每 6 小时刷新，不依赖任何第三方解析接口
- B 站走官方 API 直连（纯 HTTP），完全绕开网页反爬
- 模型从 ModelScope（国内）下载，无需科学上网

## 文件结构

```
video2text/
├── app.py               # FastAPI 主服务（任务队列 + API）
├── extractor.py         # 链接解析 / 下载 / 抽音轨（含 B 站 API 直连）
├── transcriber.py       # faster-whisper 封装（模型自动下载）
├── cookies_manager.py   # 抖音/B站 cookies 自动获取刷新
├── static/index.html    # 前端（移动端适配、一键粘贴、进度条）
├── run.bat / run.sh     # 一键启动脚本
├── requirements.txt     # Python 依赖
├── cookies/             # cookies 缓存（自动生成）
├── models/              # whisper 模型（首次运行自动下载）
├── history/             # 转写历史（JSON）
└── jobs/                # 任务临时目录（用完即清）
```

## 常见问题

**Q: 抖音解析报 "Fresh cookies are needed"？**
cookies 过期了。删除 `cookies/douyin_cookies.txt` 后重试，服务会自动重新获取。若反复失败，在项目目录执行 `python cookies_manager.py` 手动刷新。

**Q: 转写出现重复句/胡言乱语？**
多为 BGM 过重或非中文语音。普通话口播内容识别质量最好。BGM 重的视频可尝试 `WHISPER_MODEL=large-v3`。

**Q: 转写速度？**
CPU 上约为音频时长的 1~2.5 倍（medium/int8）。例：5 分钟视频约 2~5 分钟转完。有 NVIDIA 显卡可改 `transcriber.py` 里的 `device="cpu"` 为 `"cuda"`，速度提升 10 倍+。

**Q: 想部署到服务器 24 小时用？**
可以。`nohup python app.py &` 或用 systemd/supervisor 守护。注意：**不要暴露公网**（无鉴权），建议仅在局域网/内网使用，或自行为 FastAPI 加上鉴权。

## 合规提醒（重要）

本工具仅限**个人学习、自己做内容研究/文案参考**使用：
- 提取的文案版权归属原作者，商用需授权
- 请勿批量扒取他人内容用于搬运、二创发布
- 若要做成对外服务/小程序，需另行评估版权与平台条款风险（参见交付说明文档）

## 卸载

删掉整个 `video2text` 目录即可，无系统级残留。
