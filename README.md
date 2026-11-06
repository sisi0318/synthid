# SynthID Detector Python SDK & REST API

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
