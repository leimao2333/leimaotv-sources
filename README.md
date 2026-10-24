# LeimaoTV Sources

把三条上游源地址打包成**一条聚合地址**。给 [LeimaoTV](https://github.com/leimao2333/LEIMAOTV) 用，也适用于任何支持 TXT / M3U 订阅的播放器。

当前聚合结果：**538 个频道，12 个分组**，每 12 小时自动重抓一次。

---

## 一条地址

```
https://raw.githubusercontent.com/leimao2333/leimaotv-sources/main/dist/live.txt
```

国内访问 `raw.githubusercontent.com` 不顺时，换成加速前缀（路径原样保留）：

```
https://gh-proxy.com/raw.githubusercontent.com/leimao2333/leimaotv-sources/main/dist/live.txt
```

| 用途 | 文件 | 格式 |
| --- | --- | --- |
| 通用订阅（推荐） | [`dist/live.txt`](dist/live.txt) | TXT，`分组,#genre#` |
| 需要 M3U 的播放器 | [`dist/live.m3u`](dist/live.m3u) | M3U |
| 一次性导入三条原始源 | [`dist/sources.json`](dist/sources.json) | LeimaoTV 源备份 v1 |
| 每个源的状态与条数 | [`dist/manifest.json`](dist/manifest.json) | JSON（含内容指纹、过滤明细） |

---

## 三条上游源

| # | 名称 | 类型 | 地址 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | IPTV4 · 综合直播 | 直播 TXT | `https://live.zbds.top/tv/iptv4.txt` | 主力源，538 条频道里的绝大多数来自它 |
| 2 | IPTV6 · vbskycn | 直播 TXT | `https://gh-proxy.com/raw.githubusercontent.com/vbskycn/iptv/refs/heads/master/tv/iptv6.txt` | 上游目前只有存活标记，暂无可用频道 |
| 3 | jin18 · MoonTV 影视配置 | 点播加密串 | `https://raw.githubusercontent.com/hafrey1/LunaTV-config/refs/heads/main/jin18.txt` | 单行加密，需客户端内置 MoonTV 解密；**不参与频道合并** |

三条的差别决定了聚合方式：

- **1 和 2 是直播 TXT**，可以按频道粒度合并、去重、重新分组，所以能合成一份 `live.txt`。
- **3 是 MoonTV 的加密配置**，内容是一整行密文，本仓库不做解密（也不该做），只在 `sources.json` 和 README 里收录地址，供支持 MoonTV 的客户端使用。

---

## 怎么用

### LeimaoTV

推荐导入 `sources.json`——三条源各自独立进列表，某个上游挂了只影响它自己那一栏：

```
https://raw.githubusercontent.com/leimao2333/leimaotv-sources/main/dist/sources.json
```

想只吃合并结果，就把 `live.txt` 当单个直播源加进来。

### 其他播放器

TiviMate、PotPlayer、VLC、Kodi 等直接用上面「一条地址」里的 `live.txt` 或 `live.m3u`。

分组顺序已排好：央视频道 → 卫视频道 → 地方频道 → 体育 → 电影 → 电视剧 → 儿童 → 纪录 → 音乐 → 戏曲 → 数字 → 解说 → 春晚 → 直播中国 → 港澳台 → 国际 → 其他。

---

## 自动更新

`.github/workflows/update.yml` 每天 UTC 00:17 / 12:17（北京时间 08:17 / 20:17）跑一次：

1. 抓取三条上游；
2. 合并、去重、重新分组、过滤非直播条目；
3. 只在内容真的变了时提交，不会刷出一堆空 commit。判据是 `manifest.json` 里的 `fingerprint`（统计 + 分组 + 三个产物的 sha256 + 上游状态），指纹一致就原样保留旧文件。

> `manifest.json` 的 `generated_at` 是**内容最后更新时间**，不是「最后一次抓取时间」。上游没变化时它不会跳动——这样 Actions 才不会天天产出只差时间戳的提交。想知道最近一次检查，看 [Actions 运行记录](https://github.com/leimao2333/leimaotv-sources/actions)。

上游挂掉不会让整次聚合失败——单源超时/404 只记进 `manifest.json` 的 `error` 字段，其余源照常输出。

### 本地手动跑

```bash
python3 scripts/aggregate.py           # 生成 dist/
python3 scripts/aggregate.py --check   # 只抓取体检，不写文件
```

零依赖，Python 3.9+ 标准库即可。

### 加源 / 删源

只改 `config/sources.json`，别手改 `dist/`（下次自动更新会覆盖）。字段说明：

| 字段 | 说明 |
| --- | --- |
| `id` | 短标识，出现在 `manifest.json` 里 |
| `type` | `LIVE` 直播 / `VOD` 点播 |
| `format` | `txt` / `m3u` / `moontv-encrypted`（加密串，只收录不解析） |
| `merge` | `true` 才参与频道级合并 |
| `enabled` | 是否写进 `sources.json`（供客户端导入） |

---

## 目录结构

```
config/sources.json      上游清单（唯一需要人工维护的文件）
scripts/aggregate.py     抓取 + 合并 + 生成，零依赖
dist/live.txt            聚合结果 · TXT
dist/live.m3u            聚合结果 · M3U
dist/sources.json        三条原始地址，LeimaoTV 可直接导入
dist/manifest.json       源状态、条数、过滤明细、文件指纹
```

---

## 几点说明

- **过滤是透明的**：被丢掉的行都写在 `manifest.json` 的 `filtered` 里，并注明原因（存活标记行、`.mp4` 点播回放、无法解析）。想留就改 `config/sources.json` 的 `filter`。
- **只过滤 `.mp4/.m4v`，不过滤 `.flv`**：HTTP-FLV 是地方台直播的常用承载，按扩展名一刀切会误杀真直播。
- **频道名里的逗号会被换成空格**：TXT 靠第一个逗号切「名称,地址」，名字里带逗号会让解析错位。
- **本仓库只做聚合索引**，所有频道内容的版权与可用性归各上游站点所有，不存储、不转码、不分发任何视频流。上游下线或地址失效请直接找上游。
- 仅供个人学习与技术研究使用。
