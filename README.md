# LeimaoTV Sources

把多条上游源地址打包成**一条聚合地址**。给 [LeimaoTV](https://github.com/leimao2333/LEIMAOTV) 用，也适用于任何支持 TXT / M3U 订阅的播放器。

当前聚合结果：**3023 个频道、19 个分组**，其中央视频道 46 个台铺开 552 条线路、卫视频道 47 个台铺开 749 条线路——同一个台有十几条备用地址，正是客户端「线路降级 + 失效自动切换」最需要的原料。每 12 小时自动重抓一次。

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
| 需要 M3U 的播放器 | [`dist/live.m3u`](dist/live.m3u) | M3U（带 `x-tvg-url`，自动挂节目单） |
| 直播平台（独立，可选） | [`dist/platforms.txt`](dist/platforms.txt) / [`dist/platforms.m3u`](dist/platforms.m3u) | TXT / M3U |
| 四川电信 IPTV（独立，可选） | [`dist/sichuan.txt`](dist/sichuan.txt) / [`dist/sichuan.m3u`](dist/sichuan.m3u) | TXT / M3U |
| 一次性导入上游原始源 | [`dist/sources.json`](dist/sources.json) | LeimaoTV 源备份 v1 |
| 每个源的状态与条数 | [`dist/manifest.json`](dist/manifest.json) | JSON（含内容指纹、过滤明细、节目单体检） |

---

## 上游源

| # | 名称 | 类型 | 地址 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | IPTV4 · vbskycn | 直播 TXT | `raw.githubusercontent.com/vbskycn/iptv/.../tv/iptv4.txt` | 启用，610 条 |
| 2 | CCSH · 综合直播 | 直播 TXT | `raw.githubusercontent.com/CCSH/IPTV/.../main/live.txt` | 启用，2408 条；线路主力 |
| 3 | IPTV6 · vbskycn（IPv6） | 直播 TXT | `raw.githubusercontent.com/vbskycn/iptv/.../tv/iptv6.txt` | **停用**，上游是空壳（268 字节，只有一行「更新时间」占位） |
| 4 | iptv-org · 中文频道 | 直播 M3U | `iptv-org.github.io/iptv/languages/zho.m3u` | 启用，上游 218 条里**只收 10 条**（见下） |
| 5 | CCSH · 直播平台 | 直播 M3U | `raw.githubusercontent.com/CCSH/IPTV/.../main/live_platforms.m3u` | 启用，2601 条；**走独立通道**，不进主订阅 |
| 6 | 成都/四川电信 · IPTV | 直播 M3U | `iptv.zsdc.eu.org/home/iptv.m3u8` | 启用，148 条；**走独立通道**，只对四川电信宽带可播 |
| 7 | jin18 · 影视（MoonTV） | 点播加密串 | `raw.githubusercontent.com/hafrey1/LunaTV-config/.../jin18.txt` | 启用，解开是 **28 个苹果CMS 站点**；**不参与频道合并** |
| 8 | LunaTV · 影视（69 站） | 点播加密串 | `raw.githubusercontent.com/hafrey1/LunaTV-config/.../LunaTV-config.txt` | **停用**，69 个站点（jin18 的超集）；站点多 = 首屏慢，想要更多片源时再启用 |

这几条的差别决定了聚合方式：

- **1 和 2 是电视频道 TXT**，可以按频道粒度合并、去重、重新分组，所以能合成一份 `live.txt`。两者互补：CCSH 提供大量同台备用线路，vbskycn 提供另一批独立地址。
- **4 是 iptv-org 的中文语言包**，但它的台名是英文的（`8TV`、`Ando TV`、`Anhui TV`），分组也是英文的（`Religious`、`Undefined`）。本仓对它单独开了两条过滤：`drop_non_chinese_name` 丢掉既无中文也无 Chinese/Mandarin 标识的条目，`drop_geo_blocked` 丢掉 `[Geo-blocked]`。218 条因此只剩 10 条。**剩下的 10 条里 6 条是境外宗教传教频道**（Angel TV、CGNTV、God Stands、LLBN、Peace TV），实际有价值的只有凤凰卫视中文台和客家电视台——真要极致精简，把这一条 `enabled` 改成 `false` 即可，主订阅只少 10 条。
- **5 虽然也是直播**，但内容是 2600 多条「一起看」「原创IP」「zonghe」这类**平台主播直播间**，不是电视台。并进主订阅会把 600 多个电视频道直接淹掉，所以用 `output` 单独出 `dist/platforms.*`。想看电视以外的内容，单独导入这个地址。
- **6 是成都/四川电信的 IPTV 单播源**，155 条里 148 条是 `rtsp://182.139.234.40/...`，只有四川电信宽带能连（本机实测 554 端口直接超时），另外 7 条是 `http://192.168.100.1:4022/udp/...` 这种指向上传者**自家路由器**的 udpxy 地址，外人必然不通——后者由 `drop_lan_host` 过滤掉，前者按 `output` 单独出 `dist/sichuan.*`。**不并进主订阅**：给 3000 条可播线路里塞 148 条绝大多数人播不了的地址，只会让线路降级链多走几轮无谓的超时。四川电信用户单独导入这一个文件即可。
- **3 目前没有内容**。上游 `iptv6.txt` 和 `iptv6.m3u` 都只剩一行更新时间占位（实测 268 / 282 字节）。等上游恢复，把 `config/sources.json` 里的 `enabled` 改回 `true` 即可。
- **7 和 8 是 MoonTV / LunaTV 的加密影视清单**，一行 Base58，解开是 `{ "api_site": { 站点key: { name, api } } }` —— 每个 `api` 都是**标准苹果CMS `provide/vod` 接口**，所以它不是一个源，而是**一把源的集合**。本仓库不解密（也不该解密），只收录地址；客户端的 MoonTV 适配器解完码后按站点 fan-out（分类取首个可用站，列表与搜索并发取多站再合并去重），对用户呈现为「一个影视源」。
  - 默认启用 28 站的 `jin18`：站点少、首屏快。嫌片少就把第 8 条（69 站，jin18 的超集）启用、同时停用第 7 条 —— **两条都开等于把同一批站拉两遍**，还多一张重复的源卡片。

---

## 节目单（EPG）

聚合出来的 `live.m3u` 头部带了 `x-tvg-url`，播放器（Kodi / TiviMate / APTV 等）打开订阅就会自己去拉节目单：

```
https://epg.zsdc.eu.org/t.xml.gz
```

选它的理由很实际：这份 XMLTV 的 `channel id` 就是台名（`CCTV1`、`CCTV5+`、`湖南卫视`），而本仓的台名恰好也是这套写法——124 个 id 里 **77 个能逐字对上**，播放器按台名兜底匹配就能出节目单，不需要我们再维护一张 id 映射表。剩下对不上的是四川本地台（`CDTV1`、`SCTV2`）和付费专区（`CHC`、`欢笑剧场`），主要服务 `dist/sichuan.*`。

M3U 里上游自带 `tvg-id` 的条目会原样保留（目前只有 iptv-org 与四川源带），没带的**不会编一个**——编错的 id 会让节目单张冠李戴，比「这个台没有节目单」更让人困惑。

仓库本身不存节目单：XMLTV 解开 6.9 MB、一天更新好几次，每天提交一份进仓库只会把仓库撑大。脚本每次跑会抓一次，把「多少台多少档」写进 `manifest.json` 的 `epg` 段，上游哪天 404 了一眼就能看见。EPG 不进内容指纹，所以它天天变化也不会刷出空提交。

### 两个地址，按客户端挑

| 地址 | 大小 | 给谁用 |
| --- | --- | --- |
| `https://epg.zsdc.eu.org/t.xml.gz` | 0.5 MB | 写进 `live.m3u` 的 `x-tvg-url`。Kodi / TiviMate / APTV 这些播放器自己会解压，省一半流量 |
| `https://epg.zsdc.eu.org/t.xml` | 6.9 MB | 纯 XML，给不解压 `.gz` 的客户端 |

LeimaoTV 目前属于后一类：`EpgImporter` 把响应直接当文本喂给 XML 解析器，而这份 `.gz` 的 `Content-Type` 是 `application/gzip`、没有 `Content-Encoding: gzip`，OkHttp 不会自动解压——填 `.gz` 会得到「节目单损坏」。另外客户端也不读 m3u 的 `x-tvg-url`，节目单要在「源管理 → 节目单订阅地址」里单独添加。两件事都是客户端侧待补的能力，跟这个地址无关。

---

## 怎么用

### LeimaoTV

推荐导入 `sources.json`——每条源各自独立进列表，某个上游挂了只影响它自己那一栏：

```
https://raw.githubusercontent.com/leimao2333/leimaotv-sources/main/dist/sources.json
```

想只吃合并结果，就把 `live.txt` 当单个直播源加进来。

`sources.json` 里的地址是 **`gh-proxy.com` 加速版**（config 里的 `client_url`）。抓取在本仓 Actions 的海外机器上跑，直连 GitHub 最快；而这份文件是给国内电视盒子里的客户端导入的，`raw.githubusercontent.com` 在那边基本连不上。两个场景最优解不同，所以分成两个字段，不要混用。

### 其他播放器

TiviMate、PotPlayer、VLC、Kodi 等直接用上面「一条地址」里的 `live.txt` 或 `live.m3u`。

分组顺序已排好：央视频道 → 卫视频道 → 地方频道 → 体育 → 电影 → 电视剧 → 综艺 → 儿童 → 少儿 → 纪录 → 音乐 → 戏曲 → 游戏 → 数字 → 解说 → 春晚 → 直播中国 → 港澳台 → 国际 → 其他。

---

## 自动更新

`.github/workflows/update.yml` 每天 UTC 00:17 / 12:17（北京时间 08:17 / 20:17）跑一次：

1. 抓取上游；
2. 合并、去重、重新分组、过滤非直播条目；
3. 抓一次节目单，只记「能不能下、多少台多少档」，不落盘；
4. 只在内容真的变了时提交，不会刷出一堆空 commit。判据是 `manifest.json` 里的 `fingerprint`（统计 + 分组 + 全部产物的 sha256 + 上游状态），指纹一致就原样保留旧文件。

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
| `enabled` | 是否写进 `sources.json` 时默认启用的标记 |
| `url` | **抓取**用的地址（海外机器直连 GitHub） |
| `client_url` | 可选，写进 `dist/sources.json` 给国内客户端用的加速地址；不写就沿用 `url` |
| `output` | 可选，填了就不进主订阅，单独生成 `dist/<值>.txt` / `.m3u` |
| `canonical_groups` | 可选，独立通道默认保留上游原组名；填 `true` 表示这个源的组名本来就规整（`CCTV` / `卫视` / `四川`），照样走组名归一 |
| `filter` | 可选，**只对这个源**生效，覆盖全局 `filter`。见下 |

过滤强度按源配置，因为同一条规则换到别的源上会误杀：iptv-org 需要丢纯英文台名，但拿这条去套其他源，`HBO`、`Discovery` 这类正常英文名频道就没了。可用的开关有 `drop_mp4`、`drop_date_marker`、`drop_bare_domain`、`drop_geo_blocked`、`drop_non_chinese_name`、`drop_lan_host`。

`drop_lan_host` 丢的是 `192.168.x` / `10.x` / `172.16-31.x` / `127.x` 这类**指向内网**的地址。典型形态是 `http://192.168.100.1:4022/udp/239.94.0.122:5140` —— 那是源作者自己路由器上的 udpxy，外人导进来必然连不上，留着只会让线路降级链多走一轮超时。

节目单不写在 `sources` 里，而是顶层的 `epg` 数组：脚本会把 `url` 拼成 m3u 头部的 `x-tvg-url`，并抓一次做体检。

### 分组怎么归一

上游各写各的组名，不处理会散成 54 个分组（28 个省级地名各占一行）。归一分三级，顺序不能换：

1. `group_alias` 显式别名：`电影` → `电影频道`、`纪录片` → `纪录频道`、`国际台` → `国际频道`、`NewTV`/`iHOT`/`咪咕直播` → `数字频道`、`MTV` → `音乐频道`、`CCTV` → `央视频道`、`卫视` → `卫视频道`、`四川` → `地方频道`；
2. 已经是 `group_order` 里的标准名，原样保留；
3. `group_rules.local_suffix` 兜底：以「频道」结尾却不是以上任何一种，判为地方台，归入 `地方频道`。

这三条只在主订阅与声明了 `canonical_groups` 的源上生效。其余独立通道（`platforms`）保留上游原组名，否则「4K频道」会被误折进地方频道。

---

## 目录结构

```
config/sources.json      上游清单（唯一需要人工维护的文件）
scripts/aggregate.py     抓取 + 合并 + 生成，零依赖
dist/live.txt            聚合结果 · TXT
dist/live.m3u            聚合结果 · M3U（带 x-tvg-url）
dist/platforms.txt       直播平台通道 · TXT（独立，不进主订阅）
dist/platforms.m3u       直播平台通道 · M3U
dist/sichuan.txt         四川电信 IPTV 通道 · TXT（独立，不进主订阅）
dist/sichuan.m3u         四川电信 IPTV 通道 · M3U
dist/sources.json        上游地址（gh-proxy 加速版），LeimaoTV 可直接导入
dist/manifest.json       源状态、条数、过滤明细、节目单体检、文件指纹
```

---

## 几点说明

- **过滤是透明的**：被丢掉的行都写在 `manifest.json` 的 `filtered` 里，并注明原因（存活标记行、`.mp4` 点播回放、无法解析）。想留就改 `config/sources.json` 的 `filter`。
- **只过滤 `.mp4/.m4v`，不过滤 `.flv`**：HTTP-FLV 是地方台直播的常用承载，按扩展名一刀切会误杀真直播。
- **除了 `http(s)` 也认 `rtsp` / `rtmp`**：电信、联通的 IPTV 单播源基本只有 `rtsp://`，只认 http 会把整条源判成「一条都解析不出来」。认宽了也有风险，所以只加这两种，`C:\record\a.ts`、`/mnt/vod/b.ts` 这类脏行照样进 `filtered`。
- **频道名里的逗号会被换成空格**：TXT 靠第一个逗号切「名称,地址」，名字里带逗号会让解析错位。
- **地址尾部的测速列会被切掉**：上游有些行写成 `...&authid=0,0.4,720x576`，`0.4,720x576` 是延迟和分辨率，不是 URL 的一部分。清洗只认「逗号 + 纯数字（可带小数）+ 可选分辨率」这一种形态，芒果 TV 地址里合法的 `,,/xxx.m3u8` 路径不会被碰。
- **台名尾部的括号标记会被切掉**：iptv-org 写成 `Bingtuan Satellite TV (540p) [Not 24/7]`，清晰度与非全天标记对选台没有意义，剥掉后只留 `Bingtuan Satellite TV`。
- **本仓库只做聚合索引**，所有频道内容的版权与可用性归各上游站点所有，不存储、不转码、不分发任何视频流。上游下线或地址失效请直接找上游。
- 仅供个人学习与技术研究使用。
