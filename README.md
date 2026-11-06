# SynthID Detector Python SDK & REST API

> [!WARNING]
> **重要免责声明（请在使用前仔细阅读）**
> 本项目为**非官方**的第三方开源技术研究项目，与 Google LLC、Google DeepMind 及任何内容凭证合作方（OpenAI、NVIDIA 等）**不存在任何隶属、授权、认可或合作关系**。本项目所有代码与文档仅用于**网络协议逆向工程教学、AI 内容溯源学术研究与个人技术交流**，严禁用于任何商业牟利、高并发滥用、恶意爬虫或违反 [Google 服务条款](https://policies.google.com/terms) 的用途。使用本项目产生的一切风险与法律责任（包括但不限于 Google 账号被限流或封禁、数据泄露、检测结果误判等）均由使用者本人全权承担，作者不承担任何直接或间接责任。完整条款请拉至文末阅读 **[五、免责声明与法律告知](#五免责声明与法律告知)**。

Google DeepMind **SynthID Detector**（`https://synthid.com/`）的独立 Python SDK、命令行工具与本地 HTTP API 服务端。

* **零第三方依赖**：仅使用 Python 标准库，开箱即用。
* **全模态支持**：支持图片（Image）、音频（Audio）、视频（Video）的 AI 水印与 C2PA 合作方凭证检测（Google、OpenAI、NVIDIA、Kakao 等）。
* **全自动续期**：只需配置一次账号的 `refresh_token`、`push_filename` 与 `bg_token`，自动完成 Token 刷新与跨文件重复检测。
* **底层协议文档**：完整的逆向分析与 Protobuf 字段字典请参见 **[`PROTOCOL.md`](./PROTOCOL.md)**。

---

## 一、快速配置（仅需 3 个字段）

1. 将配置模板复制为 `config.json`（已加入 [`.gitignore`](./.gitignore)，不会被提交到 Git）：
   ```bash
   cp config.example.json config.json
   ```
2. 编辑 `config.json`，填入从浏览器提取的 3 个字段：
   ```json
   {
     "refresh_token": "AMf-vBw...YOUR_FIREBASE_REFRESH_TOKEN",
     "push_filename": "users/YOUR_USER_ID/files/00000000-0000-4000-8000-000000000000",
     "bg_token": "!sLOls9f...YOUR_BOTGUARD_SNAPSHOT_TOKEN"
   }
   ```

---

## 二、如何从浏览器提取账号配置

### 方式 A：浏览器 `F12 -> Console` 一键生成（推荐）

1. 在浏览器打开 `https://synthid.com/` 并登录账号。
2. 按 `F12` 打开 **Console（控制台）**，粘贴以下代码并回车：
   ```javascript
   (async () => {
     const user = await new Promise((resolve, reject) => {
       const req = indexedDB.open("firebaseLocalStorageDb");
       req.onerror = () => reject(req.error);
       req.onsuccess = () => {
         const tx = req.result.transaction("firebaseLocalStorage", "readonly");
         const all = tx.objectStore("firebaseLocalStorage").getAll();
         all.onsuccess = () => resolve(all.result.find(r => r?.value?.stsTokenManager)?.value || null);
       };
     });
     if (!user) return console.error("未检测到登录状态，请先登录！");

     let pushFilename = "", bgToken = "", lastBgToken = "";
     const tryOutput = () => {
       if (!pushFilename || !bgToken || bgToken === lastBgToken) return;
       lastBgToken = bgToken;
       const cfg = JSON.stringify({
         refresh_token: user.stsTokenManager.refreshToken,
         push_filename: pushFilename,
         bg_token: bgToken
       }, null, 2);
       console.log("%c====== 提取成功！请复制以下 JSON 保存为 config.json ======\n" + cfg, "color: #10b981; font-weight: bold;");
     };

     const checkHeader = (k, v) => {
       const key = String(k).toLowerCase();
       if (key === "push-filename" && v) {
         pushFilename = String(v).trim();
         console.log("[1/2] 已捕获上传路径 push_filename:", pushFilename);
         tryOutput();
       } else if (key === "x-goog-ext-525006476-jspb" && v) {
         try {
           const arr = JSON.parse(v);
           if (typeof arr?.[5] === "string" && arr[5].startsWith("!")) {
             bgToken = arr[5];
             console.log("[2/2] 已捕获安全令牌 bg_token:", bgToken.slice(0, 24) + "...");
             tryOutput();
           }
         } catch {}
       }
     };

     const checkBody = (bodyStr) => {
       if (typeof bodyStr === "string" && bodyStr.includes("TZRKo")) {
         const m = decodeURIComponent(bodyStr).match(/users\/[^"\\&\s]+\/files\/[0-9a-f-]+/i);
         if (m) {
           pushFilename = m[0];
           tryOutput();
         }
       }
     };

     for (const fn of ["append", "set"]) {
       const orig = Headers.prototype[fn];
       Headers.prototype[fn] = function(k, v) {
         checkHeader(k, v);
         return orig.apply(this, arguments);
       };
     }

     const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
     XMLHttpRequest.prototype.setRequestHeader = function(k, v) {
       checkHeader(k, v);
       return origSetHeader.apply(this, arguments);
     };
     const origSend = XMLHttpRequest.prototype.send;
     XMLHttpRequest.prototype.send = function(body) {
       if (typeof body === "string") checkBody(body);
       return origSend.apply(this, arguments);
     };

     const origFetch = window.fetch;
     window.fetch = function(input, init) {
       try {
         const headers = input instanceof Request ? input.headers : init?.headers;
         if (headers instanceof Headers) headers.forEach((v, k) => checkHeader(k, v));
         else if (headers && typeof headers === "object") {
           for (const [k, v] of Object.entries(headers)) checkHeader(k, v);
         }
         if (typeof init?.body === "string") checkBody(init.body);
         else if (input instanceof Request) input.clone().text().then(checkBody).catch(() => {});
       } catch {}
       return origFetch.apply(this, arguments);
     };

     console.log("已读取账号:", user.email || user.uid, "-> 请在页面上随便上传一张图触发一次检测...");
   })();
   ```
3. 在网页上**随便选择一张图片上传**，将控制台输出的 JSON 复制保存为 `config.json` 即可。

### 方式 B：浏览器 `F12` 手动查找

1. **`refresh_token`**：`F12` -> **Application** -> **IndexedDB** -> `firebaseLocalStorageDb` -> `firebaseLocalStorage` -> 展开记录中的 `value.stsTokenManager.refreshToken`。
2. **`bg_token` 与 `push_filename`**：在网页随便上传一个文件，打开 `F12` -> **Network** 筛选 **`TZRKo`**（点击 `batchexecute?rpcids=TZRKo` 请求）：
   * **Request Headers** -> `x-goog-ext-525006476-jspb`：复制数组第 6 项（以 `!` 开头的字符串）作为 `bg_token`。
   * **Payload** -> `f.req`：复制其中的 `users/{uid}/files/{uuid}` 路径作为 `push_filename`。

---

## 三、使用指南

### 1. 命令行直接调用 ([`cli.py`](./cli.py) 或 `python -m synthid`)

```bash
# 检测 Google AI 生成的测试图片
python cli.py detect samples/sample_google.jpg

# 检测 OpenAI 生成的测试图片
python cli.py detect samples/sample_openai.jpg

# 手动刷新 Firebase id_token
python cli.py refresh
```

### 2. Python SDK 调用

```python
from synthid import SynthIDClient

client = SynthIDClient.from_config("config.json")

result = client.detect_file("samples/sample_google.jpg")
print(result["status"], result["partner"])  # 输出: detected google
```

### 3. 启动本地 HTTP API 服务

```bash
python cli.py serve --host 127.0.0.1 --port 8000
```

* **健康检查**：
  ```bash
  curl http://127.0.0.1:8000/health
  ```
* **表单上传文件检测 (`multipart/form-data`)**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/detect -F "file=@samples/sample_google.jpg"
  ```
* **JSON 请求检测（支持 `file_path`、`base64` 或远程 `url`）**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/detect \
    -H "Content-Type: application/json" \
    -d '{"file_path": "./samples/sample_google.jpg"}'
  ```

---

## 四、项目结构

```text
synthid/
├── synthid/                  # 核心 Python SDK 包
│   ├── __init__.py           # 导出接口 (SynthIDClient, SynthIDError, run_server)
│   ├── __main__.py           # 模块命令行入口 (python -m synthid)
│   ├── auth.py               # JWT 解码、过期判定与凭证格式标准化
│   ├── client.py             # 核心客户端 (Token 自动续期 + Scotty 上传 + TZRKo 调用)
│   ├── constants.py          # 接口常量、MIME 映射与枚举字典
│   ├── exceptions.py         # 自定义异常定义
│   ├── parser.py             # Boq 响应帧解析与 Protobuf (JSPB) 解码器
│   └── server.py             # 多线程 HTTP REST API 服务端
├── samples/                  # 测试样本目录
│   ├── sample_google.jpg     # Google AI 原生 SynthID 水印测试图
│   └── sample_openai.jpg     # OpenAI 合作方水印测试图
├── cli.py                    # 顶层命令行入口
├── config.example.json       # 账号凭证配置模板
├── PROTOCOL.md               # 底层通信协议与 Protobuf 响应结构技术文档
└── README.md                 # 项目说明与快速上手指南
```

---

## 五、免责声明与法律告知

在使用、复制、修改或分发本仓库（以下简称“本项目”）中的任何代码、文档或附属文件之前，请务必仔细阅读并同意以下全部条款。**一旦您下载、运行、引用或以任何形式使用本项目，即视为您已充分理解并无条件接受本免责声明的全部内容；若您不同意以下任何条款，请立即停止使用并彻底删除本项目的全部副本。**

### 1. 非官方性质与商标归属
* 本项目是由独立开发者基于公开网页前端代码进行协议分析而编写的**非官方（Unofficial）**工具，与 Google LLC、Google DeepMind、Alphabet Inc.、OpenAI、NVIDIA、Kakao 及 C2PA 联盟等任何企业或组织**无任何官方关联、合作、赞助、授权或背书关系**。
* 文中提及的 `Google`、`DeepMind`、`SynthID`、`Scotty`、`Firebase`、`OpenAI` 等名称、徽标及相关商标均归其各自合法权利人所有，仅用于指代对应的技术协议或服务对象。

### 2. 用途限制（仅供学习与学术研究）
* 本项目及配套的 [`PROTOCOL.md`](./PROTOCOL.md) 文档**纯粹出于网络安全教学、前后端通信协议研究（Google Boq / Protobuf JSPB / Scotty 上传协议）及 AI 生成内容水印检测的学术探讨目的而编写**。
* **严禁将本项目用于以下任何场景**：
  1. 任何形式的**商业化运营、付费 API 售卖、SaaS 封装牟利或商业产品集成**；
  2. 未经授权的**高并发请求、批量自动化滥用、分布式抓取、DDoS 或干扰目标服务器正常运行**的行为；
  3. 绕过、破坏或对抗 AI 内容安全机制、用于洗稿/去水印对抗训练，或从事任何黑灰产与违法违规活动；
  4. 任何违反《Google 服务条款》（Google Terms of Service）、《Google API 服务用户数据政策》或使用者所在地法律法规的行为。
* 请务必在获取到知识或完成技术验证后的 **24 小时内**自行销毁相关测试配置与产生物。

### 3. 账号安全与风控风险自担
* 本项目运行依赖使用者自行在浏览器登录并提取的个人账号凭证（`refresh_token`、`push_filename`、`bg_token`）。
* 由于直接通过脚本调用 Web 端内部接口非官方推荐的公开 API 调用方式，**使用本项目可能触发 Google 自动化风控机制（包括但不限于配额限制 `429 Too Many Requests`、临时验证拦截、服务权限收回，甚至导致关联的 Google 账号被暂停或永久封禁）**。
* **强烈建议仅使用专用的测试小号进行实验**，切勿使用绑定重要个人资产的主账号。因使用本项目导致的任何账号异常、封禁或资产损失，均由使用者自行承担。

### 4. 数据隐私与内容合规风险
* 本项目本身不包含任何第三方遥测、后门或凭证收集逻辑，所有配置均保存在本地 `config.json` 中（且已默认加入 [`.gitignore`](./.gitignore)）。但请使用者务必妥善保管本地 `config.json`，切勿将其上传至公开代码仓库或分享给他人。
* 在调用检测功能时，被检测的媒体文件（图片、音频、视频）会被直接上传至 Google 官方的 Scotty 临时存储服务（`/synthid-demo-drop-zone/ttl_1d/`）。**请勿使用本项目上传任何涉及国家秘密、商业机密、个人敏感隐私、未公开版权作品或违法违规的媒体文件**，由此引发的数据泄露或合规风险由使用者自行负责。

### 5. 检测结果的局限性与非证据声明
* AI 水印与内容凭证（SynthID / C2PA）检测本质上属于概率性与统计性技术，受限于文件重编码、压缩、裁剪、降噪、转码或模型版本差异，**必然存在假阳性（误报）与假阴性（漏报）的可能**。
* 本项目输出的检测状态（`detected` / `not_detected` / `uncertain`）、置信度及热力图数据**仅供参考，不具备任何法律效力、司法鉴定效力或学术权威认定效力**。严禁将本项目输出的结果作为指控他人抄袭、学术不端、版权侵权、商业欺诈或名誉侵权的唯一或决定性依据，因采信检测结果而引发的一切纠纷与法律后果均与本项目作者无关。

### 6. 无担保与责任豁免（AS-IS）
* 本项目按**“原样”（AS IS）**提供，不附带任何明示或暗示的担保，包括但不限于对适销性、特定用途适用性、接口长期可用性、代码无缺陷以及不侵权的担保。随着 Google 前端版本迭代，本项目随时可能失效，作者没有义务提供后续维护或更新支持。
* 在任何情况下，本项目作者及贡献者均**不对因使用或无法使用本项目而产生的任何直接、间接、偶然、特殊、惩罚性或后果性损害（包括但不限于利润损失、业务中断、数据丢失、账号封禁、名誉损害或第三方索赔）承担任何民事、行政或刑事责任**，即使事先已被告知发生此类损害的可能性。

### 7. 知识产权与侵权移除声明
* 如果相关权利方（如 Google LLC / Google DeepMind）认为本项目的开源代码或技术文档侵犯了您的合法权益或违反了相关合规要求，请通过 GitHub Issues 与仓库维护者联系，维护者将在收到有效通知后第一时间予以配合并删库/移除相关内容。
