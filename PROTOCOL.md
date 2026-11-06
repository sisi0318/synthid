# SynthID Detector (`https://synthid.com/`) 底层接口与协议技术文档

本文档详细记录 Google DeepMind **SynthID Detector**（`https://synthid.com/`）的前后端架构、身份认证、反滥用校验、Scotty 媒体上传协议以及 Boq `batchexecute` (`TZRKo`) 的 Protobuf (JSPB) 字段映射规范。

---

## 一、整体架构与通信时序

`https://synthid.com/` 基于 Google 内部 **Boq / Wiz + Angular Signals** 构建（应用标识 `WatermarkApp`，后端服务 `boq_deepmind-visualization-deepmind-uiserver`，接口前缀 `/_/WatermarkApp`）。

一次完整的水印检测包含以下 4 个阶段：

```mermaid
sequenceDiagram
    participant Client as 客户端 (SDK / 浏览器)
    participant Auth as securetoken.googleapis.com
    participant Scotty as content-push.googleapis.com
    participant Boq as synthid.com (batchexecute)

    Note over Client,Auth: 1. 检查 JWT exp，缺失或过期时通过 refresh_token 自动换取新 id_token
    Client->>Auth: POST /v1/token?key=AIzaSyCjAD... (grant_type=refresh_token)
    Auth-->>Client: 200 OK (返回 id_token, expires_in=3600, user_id)

    Note over Client,Scotty: 2. Scotty 第一阶段：初始化断点续传会话
    Client->>Scotty: POST /upload/ (X-Goog-Upload-Command: start, Push-Filename: users/{uid}/files/{uuid})
    Scotty-->>Client: 200 OK (Header: X-Goog-Upload-URL)

    Note over Client,Scotty: 3. Scotty 第二阶段：上传文件二进制字节流
    Client->>Scotty: POST {X-Goog-Upload-URL} (X-Goog-Upload-Command: upload, finalize)
    Scotty-->>Client: 200 OK (Body: /synthid-demo-drop-zone/ttl_1d/users/{uid}/files/{uuid})

    Note over Client,Boq: 4. 调用 Boq RPC (rpcids=TZRKo) 执行水印检测
    Client->>Boq: POST /_/WatermarkApp/data/batchexecute?rpcids=TZRKo (Header: X-Goog-Ext-525006476-Jspb)
    Boq-->>Client: 200 OK (返回 JSPB 数组：状态码、合作方、置信度、热力图或时间轴区间)
```

---

## 二、身份认证与 Token 续期机制 (Firebase Auth)

* **Firebase 项目配置**：
  * `apiKey`: `AIzaSyCjADz3UffE3DF5HZ--7MgVEXrbt6Bgcck`
  * `authDomain`: `synthid-app.firebaseapp.com`
* **支持的 OAuth 提供商**：
  * Google (`google.com`)
  * Apple (`apple.com`)
  * ChatGPT / OpenAI (`oidc.openai`)
* **OAuth 登录交换接口**：
  * `POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key=AIzaSyCjADz3UffE3DF5HZ--7MgVEXrbt6Bgcck`
  * 返回 `localId`（即 `user_id`）、`idToken`（有效期 3600 秒的 RS256 JWT）与长期有效的 `refreshToken`。
* **Token 自动续期接口**：
  * **Endpoint**：`POST https://securetoken.googleapis.com/v1/token?key=AIzaSyCjADz3UffE3DF5HZ--7MgVEXrbt6Bgcck`
  * **Headers**：`Content-Type: application/x-www-form-urlencoded`
  * **Body**：`grant_type=refresh_token&refresh_token={refresh_token}`
  * **Response**：
    ```json
    {
      "access_token": "eyJhbGciOiJSUzI1NiIs...",
      "expires_in": "3600",
      "token_type": "Bearer",
      "refresh_token": "AMf-vBw...",
      "id_token": "eyJhbGciOiJSUzI1NiIs...",
      "user_id": "YOUR_USER_ID",
      "project_id": "synthid-app"
    }
    ```

---

## 三、反滥用快照与 `Push-Filename` 绑定复用原理

### 1. 前端 WAA / BotGuard 初始化流程
页面加载时，前端初始化 `OK` 与 `FH` 服务：
1. 请求 `POST https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create`（携带 `X-Goog-Api-Key: AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE` 和公钥标识 `"UzdguikIn08nBCK5a0xw"`）。
2. 加载返回的 `/js/bg/...js` BotGuard 字节码脚本。
3. 在发起检测 RPC 前，调用 `FH.prototype.snapshot({ Hv: { blobref: blobId } })` 生成签名令牌 `bg_token`。

### 2. 请求头 `X-Goog-Ext-525006476-Jspb` 结构
`batchexecute` 不使用标准 `Authorization: Bearer` 头，而是通过 Protobuf 扩展字段 `525006476`（消息类 `_.iK`）传递身份与反滥用快照：

```json
[
  "<Field 1 (索引 0): Firebase JWT id_token>",
  null,
  null,
  null,
  null,
  "<Field 6 (索引 5): BotGuard 快照令牌 (!sLOls9f...)>"
]
```

### 3. 为什么单次提取的 `(push_filename, bg_token)` 可以无限次跨文件复用？
1. **路径确定性**：Scotty 上传接口返回的 `blobId` 固定为 `/synthid-demo-drop-zone/ttl_1d/` + 请求头中的 `Push-Filename`（即 `users/{uid}/files/{uuid}`）。
2. **用后即焚机制**：每次 `TZRKo` 检测完成后，后端会自动清理 drop-zone 中的该临时文件（若对已消费的 `blobId` 再次调用 `TZRKo` 会返回 gRPC 错误码 `13 INTERNAL`）。
3. **签名匹配复用**：由于 `bg_token` 内部签名绑定的是 `{ blobref: "/synthid-demo-drop-zone/ttl_1d/users/{uid}/files/{uuid}" }`，只要每次上传新文件（无论是图片、音频还是视频）时在 Scotty 请求头中**固定传入同一个 `Push-Filename`**，Scotty 就会将新文件写入同一个 `blobId` 路径，从而与 `bg_token` 内的 `blobref` 签名保持一致。

---

## 四、媒体上传协议 (Scotty Resumable Upload)

### 1. 本地校验规则
* **最大文件体积**：`104,857,600` 字节（100 MB）。
* **支持的文件类型**：
  * **Image**：`jpg`, `jpeg`, `png`, `bmp`, `webp`, `avif`, `heic`, `heif`, `tiff`, `tif`, `gif`
  * **Audio**：`wav`, `mp3` (`audio/mpeg`), `ogg`, `flac`, `aac`, `m4a` (`audio/mp4`)
  * **Video**：`mp4`, `mov` (`video/quicktime`), `webm`

### 2. 两阶段断点续传交互
* **内置认证头**：
  * `Authorization: Basic amI5dChNbS0qNjFlczJJVzpyZyRHNUp1QENpV29OJVNS`
  * `Push-ID: feeds/szvztmw263lqqp`

#### 第一阶段：初始化上传会话 (`start`)
```http
POST https://content-push.googleapis.com/upload/ HTTP/2
Authorization: Basic amI5dChNbS0qNjFlczJJVzpyZyRHNUp1QENpV29OJVNS
Push-ID: feeds/szvztmw263lqqp
Push-Filename: users/{user_id}/files/{file_uuid}
X-Goog-Upload-Protocol: resumable
X-Goog-Upload-Command: start
X-Goog-Upload-Header-Content-Length: {file_size_in_bytes}
Content-Type: application/x-www-form-urlencoded;charset=UTF-8

file_name {original_filename}
```
* **响应头**：返回 `x-goog-upload-status: active` 与上传会话地址 `x-goog-upload-url`。

#### 第二阶段：上传二进制流并结束 (`upload, finalize`)
```http
POST {x-goog-upload-url} HTTP/2
Authorization: Basic amI5dChNbS0qNjFlczJJVzpyZyRHNUp1QENpV29OJVNS
Push-ID: feeds/szvztmw263lqqp
Push-Filename: users/{user_id}/files/{file_uuid}
X-Goog-Upload-Command: upload, finalize
X-Goog-Upload-Offset: 0
Content-Type: application/x-www-form-urlencoded;charset=UTF-8

<RAW_BINARY_FILE_BYTES>
```
* **响应头**：`x-goog-upload-status: final`
* **响应体（纯文本）**：
  ```text
  /synthid-demo-drop-zone/ttl_1d/users/{user_id}/files/{file_uuid}
  ```

---

## 五、水印检测 RPC 协议 (`batchexecute` / `TZRKo`)

### 1. 请求报文格式
* **Endpoint**：`POST https://synthid.com/_/WatermarkApp/data/batchexecute?rpcids=TZRKo&source-path=%2Fimage-detection&hl=en-US&rt=c`
* **Headers**：
  ```http
  Content-Type: application/x-www-form-urlencoded;charset=utf-8
  X-Same-Domain: 1
  X-Goog-Ext-525006476-Jspb: ["<id_token>",null,null,null,null,"<bg_token>"]
  Origin: https://synthid.com
  Referer: https://synthid.com/
  ```
* **Form Body (`f.req`)**：
  * **图片 / 音频 (`_.s7(blobId, mimeType)`)**：
    ```json
    [[["TZRKo","[null,[[[\"image/jpeg\",null,null,null,null,null,\"/synthid-demo-drop-zone/ttl_1d/users/...\"]]],0]",null,"generic"]]]
    ```
  * **视频 (`Kea`)**（额外在顶层请求消息 `_.T0` 的 `field 7` 传入视频帧率 `fps`，默认 `30`）：
    ```json
    [[["TZRKo","[null,[[[\"video/mp4\",null,null,null,null,null,\"/synthid-demo-drop-zone/ttl_1d/users/...\"]]],0,null,null,null,30]",null,"generic"]]]
    ```

---

### 2. 响应报文与 Protobuf (JSPB) 字段映射

去掉响应头部的防 XSSI 前缀 `)]}'` 后，提取 `["wrb.fr", "TZRKo", "<inner_json>", null, null, <error_ext>, "generic"]`：

#### (1) RPC 错误码 (`<error_ext>`)
当 `<inner_json>` 为 `null` 时，`<error_ext>` 返回 gRPC 错误码数组：
* `[3]` (`INVALID_ARGUMENT`)：请求参数格式错误或缺失 `bg_token`。
* `[7]` (`PERMISSION_DENIED`)：`bg_token` 签名与请求的 `blobId` 不匹配。
* `[13]` (`INTERNAL`)：`blobId` 对应的临时文件不存在或已被前一次检测消费。
* `[16]` (`UNAUTHENTICATED`)：`id_token` 无效或已过期。

#### (2) 正常响应顶层数组 `d = JSON.parse(<inner_json>)`

| 顶层索引 | Protobuf 字段 | 含义 |
| :--- | :--- | :--- |
| `d[4]` | `field 5` (`repeated`) | 检测结果列表 `_.X7(d)`，取首元素 `e = d[4][0]` |
| `d[6]` | `field 7` | 配额超限信息 `_.U7`，其中 `d[6][0][2]` 为配额类型（`1/2`=image, `3/4`=audio, `5/6`=video），`d[6][0][3][0]` 为冷却等待秒数 |

#### (3) 检测结果对象 `e = d[4][0]` 字段结构

| 数组索引 | Protobuf 字段 | 适用模态 | 含义与取值规则 |
| :--- | :--- | :--- | :--- |
| **`e[0]`** | `field 1` | 全部 | **主检测状态码 (`status_code`)**：<br>• `3` = **`detected`**（检出水印）<br>• `2` = **`not_detected`**（未检出水印）<br>• `1` = **`uncertain`**（无法判定，具体原因见 `e[9]`） |
| **`e[1]`** | `field 2` | Audio | 音频检出水印的时间段列表：`[[start_sec, end_sec], ...]` |
| **`e[3]`** | `field 4` | Image | 假阳性率指标 $c$，置信度百分比 $= 100 - c$：<br>• $\ge 99.999$ -> `"Very High"`<br>• $\ge 99.9$ -> `"High"`<br>• $\ge 99.7$ -> `"Mid"` |
| **`e[6]`** | `field 7` | Image | 长度为 $N$ 的空间热力图 logit 数组。前端将其还原为 $\sqrt{N}\times\sqrt{N}$ 网格，并对每个单元格 logit $x$ 计算 Sigmoid 概率：<br>$$p = \frac{1}{1+e^{-x}}$$<br>• $p > 0.8$：`detected`<br>• $p < 0.2$：`not_detected`<br>• $0.2 \le p \le 0.8$：`uncertain` |
| **`e[7]`** | `field 8` | Video | 视频双轨检测结果 `[audio_segments, video_segments, has_audio_track]`：<br>• `e[7][0]`：音轨水印时间段 `[[start_sec, end_sec], ...]`<br>• `e[7][1]`：视频画面轨水印时间段 `[[start_sec, end_sec], ...]`<br>• `e[7][2]`：布尔值，视频是否包含可分析音轨 |
| **`e[9]`** | `field 10` | 全部 | 当 `e[0] === 1` 时的**无法判定细分原因码 (`abstention_code`)**：<br>• `1`: `"empty"`（空文件）<br>• `2`: `"low_resolution"`（分辨率过低）<br>• `3`: `"not_enough_detail"`（细节不足）<br>• `4`: `"low_fps"`（视频低于 10 FPS）<br>• `5`: `"low_quality"`（质量过低）<br>• `6`: `"possible_tampering"`（疑似篡改或仅局部使用 AI）<br>• `7`: `"audio_too_long"` / `"video_too_long"`（时长超限）<br>• `8`: `"media_too_short"`（时长过短） |
| **`e[10]`** | `field 11` | 全部 | 当 `e[0] === 3` 时的**水印来源类型**：<br>• `1` = Google 原生 SynthID 水印（`"google"`）<br>• `2` = 第三方合作伙伴水印/C2PA（具体名称见 `e[11]`） |
| **`e[11]`** | `field 12` | 全部 | 合作方标识字符串（如 `"openai"`, `"nvidia"`, `"kakao"`） |
| **`e[12]`** | `field 13` | Image | 置信度等级枚举：`6` = `"Very High"`，`5` = `"High"`，`4` = `"Mid"` |
