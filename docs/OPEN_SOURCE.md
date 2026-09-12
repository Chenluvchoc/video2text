# 开源生态调研与完善路线

> 2026-09 调研。结论先行：**本项目的"yt-dlp + 自动刷新 cookies"抖音路线目前有效**（实测跑通），
> 而部分同类项目的纯 HTTP 解析路线已因抖音风控升级失效。以下是可借鉴的项目与具体改进点。

## 同类项目对比

| 项目 | Stars | 技术路线 | 现状（2026-09） |
|---|---|---|---|
| 本项目 video2text | - | yt-dlp + playwright 自动 cookies + 本地 whisper | ✅ 实测有效 |
| [yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) | 1.3k | iesdouyin 分享页 SSR 解析 + 硅基流动云 API | ⚠️ SSR 解析 2026-04 起部分失效（见其 issue「播放视频的地址彻底失效了」） |
| [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | 20k | web API + 签名算法 + 身份池轮换 + Docker | ✅ 活跃维护，解析层最强 |
| [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) | 15.9k | JS 实现，数据采集为主 | 活跃 |
| [Norsico/Video-Materials-AutoGEN-Workstation](https://github.com/Norsico/Video-Materials-AutoGEN-Workstation) | 1.6k | ASR + TTS + AI 文案的内容生产工作站 | 活跃，形态可对标 |

## 本项目的差异化优势

1. **本地转写零成本**：竞品 douyin-mcp-server 用云 API（SenseVoice，按量计费）；本项目 whisper 本地跑，断网可用
2. **解析路线当前有效**：实测验证，且 cookies 自动刷新机制可对抗风控滚动升级
3. **极简部署**：竞品 Evil0ctal 需要 Docker + PostgreSQL + 身份池配置；本项目 `run.bat` 双击即用

## 值得借鉴的改进点（Roadmap）

### 来自 Evil0ctal/Douyin_TikTok_Download_API（工程架构）
- [ ] **风控错误分类**：区分"接口结构变了"（UpstreamChanged，需改代码）与"被风控了"（UpstreamRiskControl，重试/换身份即可），避免用户无效重试
- [ ] **Docker 一键部署**：`docker compose up` 起全套（含模型卷挂载）
- [ ] **纯函数解析器 + replay 测试**：把响应解析与网络请求解耦，录制真实响应做回归测试

### 来自 yzfly/douyin-mcp-server（产品形态）
- [ ] **MCP Server 接口**：把"提取文案"封装成 MCP 工具，可直接接入 Claude Desktop / 其他 AI 客户端对话使用（`mcp` 协议层很薄，FastAPI 项目加一个 stdio/sse 入口即可）
- [ ] **超长音频自动分段**：>1 小时音频切 10 分钟段转写再拼接，控制内存峰值

### 来自 jhj0517/Whisper-WebUI（转写功能）
- [ ] **SRT/ASS 字幕导出**：分段时间戳数据已有，导出字幕格式约 30 行代码
- [ ] **说话人分离**（可选）：接 pyannote，多人对话视频区分说话人

### 来自 SocialSisterYi/bilibili-API-collect（B 站能力扩展）
- [ ] 番剧/影视区支持（当前 playurl 只覆盖 UGC）
- [ ] 收藏夹/合集批量转写（输入收藏夹 URL → 批量出文案，编导拆解刚需）
- [ ] 分 P 视频指定 P 转写

### 来自 YouDub-webui（内容创作方向）
- [ ] 转写文本 → LLM 一键生成「选题/钩子/脚本结构」分析（贴合编导工作流）

## 已知风险与对策

| 风险 | 对策 |
|---|---|
| 抖音风控升级导致 yt-dlp 抖音解析失效 | yt-dlp 社区更新极快（`pip install -U yt-dlp` 常可解决）；极端情况参考 Evil0ctal 的签名方案重写 |
| 分享页 SSR 路线再次变化 | 本项目不依赖该路线，多路线互为备份 |
| B 站 API 参数调整 | bilibili-API-collect 文档社区持续更新，对照修 `extractor.py` 的 `bili_extract` |
