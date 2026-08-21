# jiuwenbox

`jiuwenbox` 是一個輕量級 Linux 沙箱服務，用於在分層隔離環境中執行
agent 工具和程式碼片段。

它提供一個 FastAPI 服務，用於管理沙箱生命週期、檔案傳輸、檔案
列表/搜尋以及命令執行。每個沙箱命令都會透過一個小型 supervisor
程序啟動，由 supervisor 根據配置好的隔離策略應用沙箱限制。

## 功能特性

- 基於 `bubblewrap` 的程序隔離
- 基於靜態 policy 的檔案系統訪問控制
- 透過 `sandbox_workspace` 配置沙箱後端工作目錄
- 可選的 Linux 網路名稱空間和防火牆網路隔離
- 名稱空間和 Linux capability 控制
- 在核心支援時啟用 Landlock 檔案系統約束
- Seccomp 系統呼叫過濾
- 在執行時存在時支援 Python 和 JavaScript 程式碼執行
- 審計日誌和持久化的沙箱生命週期狀態
- 推理隱私代理，用於 LLM API 請求路由和自動 API 金鑰注入

## 架構

- `server`
  - FastAPI 應用，負責沙箱生命週期管理、policy 載入、審計日誌和 API 路由。
- `server/runtime`
  - 執行時適配層，負責為每個沙箱命令啟動一個 supervisor 程序。
- `server/proxy_manager`
  - 管理推理隱私代理，用於 LLM API 路由和 API 金鑰注入。
- `server/policy_reader`
  - 共享 policy 檔案讀取器，供沙箱和代理管理器使用。
- `supervisor`
  - 每條命令的啟動器，負責將生效的 policy 轉換為 `bubblewrap`、Landlock、
    seccomp 和名稱空間配置。
- `proxy`
  - HTTP 推理隱私代理，支援路徑路由和 API 金鑰注入（支援 OpenAI 和 Anthropic 格式）。
- `models`
  - 基於 Pydantic 的 policy、沙箱、API 響應和通用狀態結構模型。

## 環境要求

- Linux
- Python 3.11+
- `bubblewrap`
- 使用 `network.mode: isolated` 時需要 `iproute2`、`iptables` 和 `nftables`
- 啟用 Landlock 和 seccomp 時需要核心支援對應能力
- 如果需要執行 JavaScript，則需要 `nodejs`

Ubuntu 安裝示例：

```bash
sudo apt-get update
sudo apt-get install -y bubblewrap iproute2 iptables nftables python3-pip python3-venv nodejs
```

## 安裝

```bash
cd jiuwenclaw/jiuwenbox
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip build
python3 -m build --wheel
python3 -m pip install dist/jiuwenbox-*.whl
```

## 啟動服務

### 本地啟動

設定預設 policy 路徑，並透過 `python -m` 啟動已安裝的服務：

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/default-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 8321 --log-level debug
```

如需使用其他 policy 或埠，可修改環境變數或 uvicorn 引數：

```bash
export JIUWENBOX_POLICY_PATH="$(pwd)/configs/jiuwenclaw-policy.yaml"
sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app --host 0.0.0.0 --port 9000 --log-level debug
```

服務會從以下環境變數讀取預設 policy 路徑：

```bash
JIUWENBOX_POLICY_PATH=/absolute/path/to/policy.yaml
```

如果程序管理器會使用環境變數渲染 uvicorn 命令，也可以設定：

```bash
JIUWENBOX_PORT=9000
```

### Docker 啟動

構建映象：

```bash
cd jiuwenclaw/jiuwenbox/scripts
sudo ./build_docker.sh
```

使用預設 policy 執行：

```bash
sudo ./run_docker.sh
```

## Policy 檔案

服務啟動時會載入一個靜態預設 policy。當前不啟用 policy 動態更新功能。

重要欄位：

- `sandbox_workspace`
  - 用於服務端管理沙箱後端儲存的宿主機目錄。
  - 該值在展開 `~` 和環境變數之後必須是絕對路徑。
- `filesystem_policy.directories`
  - 由服務端建立並在沙箱生命週期內繫結到沙箱中的目錄。
- `filesystem_policy.read_only`
  - 沙箱內授予只讀訪問許可權的路徑；這些條目本身不會掛載 host 路徑。
- `filesystem_policy.read_write`
  - 沙箱內授予讀寫訪問許可權的路徑；需要透過 `directories` 或 `bind_mounts`
    讓這些路徑實際存在於沙箱內。
- `filesystem_policy.bind_mounts`
  - 顯式的宿主機到沙箱路徑的 bind mount 配置。
- `filesystem_policy.device`
  - 使用 `bwrap --dev-bind` 暴露到沙箱內的顯式裝置節點。

路徑欄位支援 shell 風格的展開，例如 `~` 和環境變數。

最小示例：

```yaml
version: 1
name: "example"
sandbox_workspace: "/sandbox"

filesystem_policy:
  directories:
    - path: "/tmp"
      permissions: "1777"
  read_only:
    - "/bin"
    - "/sbin"
    - "/usr"
    - "/lib"
    - "/lib64"
    - "/etc"
  read_write:
    - "/tmp"
  bind_mounts:
    - host_path: "/bin"
      sandbox_path: "/bin"
      mode: "ro"
    - host_path: "/sbin"
      sandbox_path: "/sbin"
      mode: "ro"
    - host_path: "/usr"
      sandbox_path: "/usr"
      mode: "ro"
    - host_path: "/lib"
      sandbox_path: "/lib"
      mode: "ro"
    - host_path: "/lib64"
      sandbox_path: "/lib64"
      mode: "ro"
    - host_path: "/etc/resolv.conf"
      sandbox_path: "/etc/resolv.conf"
      mode: "ro"
    - host_path: "/etc/hosts"
      sandbox_path: "/etc/hosts"
      mode: "ro"
    - host_path: "/etc/nsswitch.conf"
      sandbox_path: "/etc/nsswitch.conf"
      mode: "ro"
    - host_path: "/etc/host.conf"
      sandbox_path: "/etc/host.conf"
      mode: "ro"
    - host_path: "/etc/ssl/certs"
      sandbox_path: "/etc/ssl/certs"
      mode: "ro"
    - host_path: "/etc/ssl/openssl.cnf"
      sandbox_path: "/etc/ssl/openssl.cnf"
      mode: "ro"
  device:
    - host_path: "/dev/null"
      sandbox_path: "/dev/null"

process:
  run_as_user: sandbox
  run_as_group: sandbox

namespace:
  user: true
  pid: true
  ipc: true
  cgroup: true
  uts: true

capabilities:
  add: []
  drop: []

landlock:
  compatibility: best_effort

syscall:
  x86_64:
    blocked:
      - "ptrace"
      - "mount"
      - "umount2"
      - "reboot"
      - "kexec_load"
  arm64:
    blocked:
      - "ptrace"
      - "mount"
      - "umount2"
      - "reboot"
      - "kexec_load"

network:
  mode: isolated
  egress:
    default: allow
    allowed_domains: []
    blocked_domains: []
    allowed_ips:
      - "127.0.0.1/32"
      - "::1/128"
    blocked_ips: []
    allowed_ports:
      - 443
      - 80
    blocked_ports:
      - 22
  ingress:
    default: deny
    allowed_domains: []
    blocked_domains: []
    allowed_ips:
      - "127.0.0.1/32"
      - "::1/128"
    blocked_ips: []
    allowed_ports: []
    blocked_ports:
      - 22
```

## 推理隱私代理

推理隱私代理用於在邊緣伺服器上安全訪問 LLM API：

- 路徑路由到不同 LLM 提供商（OpenAI、Anthropic、自定義）
- 自動 API 金鑰注入（OpenAI `Authorization: Bearer`、Anthropic `X-Api-Key`）
- 透過 REST API 熱插拔（建立/啟動/停止/重啟/更新/刪除）
- 透過 policy YAML 配置或REST API 管理

**架構說明**：

服務端執行一個全域性代理程序，監聽單一 host:port。

**隱私路由預設 `listen_port=0`（禁用）**，啟用時需同時配置 `listen_host`（IP 地址）和 `listen_port`。

透過 `path_prefix`區分路由（轉發規則）。**每條路由有獨立狀態**（`running` = 啟用轉發流量；`stopped` = 禁用）。

**透過 API 建立路由需 `listen_host` 有效且 `listen_port > 0`**，否則返回錯誤。

### 代理配置

配置檔案yaml檔案說明：

```yaml
inference_privacy_proxies:
  listen_host: ipaddress，繫結的 IP 地址  # 必須
  listen_port: number：監聽埠號         # 必須，非 0 值啟用代理

  # 選填，可在啟動後透過RESTAPI管理
  routes:
   - path_prefix: str，轉發規則的路徑名稱
      target_endpoint: URL，目標端點
      api_key: str，轉發時用於替換的api key
      skip_cert_verify: boolean，僅當target_endpoint為https且證書為自簽名時跳過證書校驗，除錯用
```

### URL 路由

將
http://\<listening_host\>:\<listening_port\>/\<path_prefix\>/\<api_path\>
轉發至
\<target_endpoint\>/\<api_path\>

### API 金鑰注入

- OpenAI:     將 `Authorization: Bearer <placeholder>` 替換為實際金鑰
- Anthropic: 將 `X-Api-Key: <placeholder>` 替換為實際金鑰

### 配置示例

`注意：以下網路端點地址 https://api.openai.com、http://192.168.1.100:9000 均為示例`

#### 配置檔案yaml示例

```yaml
inference_privacy_proxies:

  listen_host: "127.0.0.1"
  listen_port: 8080
  
  routes:
    - path_prefix: "openai"
      target_endpoint: "https://api.openai.com"
      api_key: "sk_sandbox_managed_openai_key"
   - path_prefix: "custom"
      target_endpoint: "http://192.168.1.100:9000"
      api_key: "sk_sandbox_managed_custom_key"
```

邊緣伺服器可使用 `listen_host: "0.0.0.0"` 接收所有網路介面的連線。

#### 轉發示例

```text
客戶端請求:  POST http://127.0.0.1:8322/openai/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
代理轉發:    POST https://api.openai.com/v1/chat/completions       -H "Authorization: Bearer sk_sandbox_managed_openai_key"

客戶端請求:  POST http://127.0.0.1:8322/custom/v1/chat/completions -H "Authorization: Bearer sk_fake_key"
代理轉發:    POST http://192.168.1.100:9000/v1/chat/completions    -H "Authorization: Bearer sk_sandbox_managed_custom_key"
```

#### jiuwenclaw配置示例


| 配置項    | 舊值                          | 新值                             |
| --------- | ----------------------------- | -------------------------------- |
| api\_base | http://192.168.1.100:9000/v1/ | http://127.0.0.1:8322/custom/v1/ |
| api\_key  | sk_sandbox_managed_custom_key | sk_fake_key                      |

## 執行整合測試

執行指定 policy 對應的整合測試：

```bash
./tests/test.sh default # jiuwenbox 使用 default-policy.yaml 作為安全策略執行服務。
./tests/test.sh yuanrong # jiuwenbox 使用 yuanrong.yaml 執行服務，代理監聽埠 8322。
```

執行指定測試用例：

```bash
python3 -m pytest tests/integration/test_server_api_default.py::TestPolicyEnforcement::test_network_mode_isolated_blocks_http_requests -s --server-endpoint 127.0.0.1:8321
```

### 效能測試

執行日常辦公 workload 效能測試：

```bash
./tests/test.sh performance --server-endpoint 127.0.0.1:8321
```

可透過指令碼引數設定沙箱數量、每個沙箱內的併發數，以及每個任務的迴圈次數：

```bash
./tests/test.sh performance \
  --sandbox-count 2 \
  --concurrency 16 \
  --loop 8 \
  --server-endpoint 127.0.0.1:8321
```

指令碼會把這些引數對映為效能測試 fixture 使用的環境變數：

| 指令碼引數 | 環境變數 | 預設值 |
| -------- | -------- | ------ |
| `--sandbox-count` | `JIUWENBOX_PERF_SANDBOX_COUNT` | `1` |
| `--concurrency` | `JIUWENBOX_PERF_CONCURRENCY` | `4` |
| `--loop` | `JIUWENBOX_PERF_LOOP` | `8` |

### 真實 LLM 整合測試

執行真實 LLM 整合測試需設定以下環境變數，若未設定環境變數，這些測試預設跳過：

```bash
export JIUWENBOX_TEST_LLM_ENDPOINT="https://api.openai.com"
export JIUWENBOX_TEST_LLM_API_KEY="sk_sandbox_managed_key"
export JIUWENBOX_TEST_LLM_MODEL="YOUR_MODEL"
```

## 注意事項

- 修改啟動 policy 檔案後，需要重啟服務。
- 已存在的沙箱會繼續使用建立時寫入的 policy。
- `/exec` API 會把命令 stderr 作為命令執行結果返回；如果服務端診斷日誌
  可能汙染命令 stderr，應使用 debug 級別日誌。

## License

Apache-2.0
