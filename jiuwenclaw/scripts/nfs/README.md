# Linux 跨節點 NFS 使用說明

這兩個指令碼用於在 Linux 節點之間共享 `jiuwenclaw` 團隊共享工作空間。

適用場景：

- 一箇中心節點作為 NFS server
- 一個或多個節點作為 NFS client
- 多個節點共享一個或多個工作空間目錄
- 分散式 Team 場景下，只共享 `team.workspace.root_path` 指向的團隊共享目錄，不要共享 `.agent_teams`

預設共享目錄：

```text
${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}
```

建議：

- 優先使用內網 IP
- 所有節點都先完成一次 `jiuwenclaw` 初始化
- 儘量使用同一個使用者執行
- 如果要使用自定義掛載點，在所有節點設定相同的 `JIUWEN_TEAM_WORKSPACE_ROOT`

## 1. 服務端執行

在中心節點執行（可重複傳入多個 `--client-ip`）：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <客戶端1內網IP> \
  --client-ip <客戶端2內網IP>
```



如果要自定義路徑：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <客戶端1內網IP> \
  --client-ip <客戶端2內網IP> \
  --export-dir /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --mount-point /mnt/jiuwenclaw/shared_workspace/jiuwen_team
```

如果只有一個客戶端，保留單個 `--client-ip` 也可以。

如果要共享多個目錄（每組 `--export-dir` 必須對應一組 `--mount-point`）：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <客戶端1內網IP> \
  --client-ip <客戶端2內網IP> \
  --export-dir /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --mount-point /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --export-dir /mnt/jiuwenclaw/shared_artifacts \
  --mount-point /mnt/jiuwenclaw/shared_artifacts
```

## 2. 客戶端執行

在每個客戶端節點執行：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh --server-ip <服務端內網IP>
```



如果要自定義路徑：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh \
  --server-ip <服務端內網IP> \
  --export-dir /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --mount-point /mnt/jiuwenclaw/shared_workspace/jiuwen_team
```

如果要掛載多個目錄（引數成對出現）：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh \
  --server-ip <服務端內網IP> \
  --export-dir /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --mount-point /mnt/jiuwenclaw/shared_workspace/jiuwen_team \
  --export-dir /mnt/jiuwenclaw/shared_artifacts \
  --mount-point /mnt/jiuwenclaw/shared_artifacts
```

## 3. 連通性檢查

在客戶端執行：

```bash
rpcinfo -p <服務端內網IP>
showmount -e <服務端內網IP>
```

如果兩條命令都能正常返回，就說明 NFS 服務已經可達。

## 4. 掛載後檢查

在客戶端執行：

```bash
mount | grep jiuwen_team
df -h | grep jiuwen_team
```

## 5. 同步驗證

在服務端執行：

```bash
echo hello > "${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}/hello.txt"
```

在客戶端執行：

```bash
cat "${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}/hello.txt"
```

再在客戶端追加：

```bash
echo world >> "${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}/hello.txt"
```

回到服務端檢視：

```bash
cat "${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}/hello.txt"
```

如果兩邊都能看到相同內容，就說明同步成功。

## 6. 說明

- 客戶端指令碼會在掛載前備份已有本地目錄
- 如果有多個客戶端，每個客戶端都執行一次客戶端指令碼即可
- 支援多客戶端和多目錄；多目錄時 `--export-dir` 與 `--mount-point` 數量必須一致
- 這套方案共享的是檔案系統，不是多節點分散式執行時
- `.agent_teams` 儲存 team.db、成員 workspace、symlink 等本地執行狀態，不應透過 NFS 在多個 teammate 之間共享

## 7. 取消掛載與回滾

服務端回滾（刪除指令碼匯出並過載 export）：

```bash
sudo bash scripts/nfs/teardown_nfs_server.sh
```

如果還要同時停止並禁用 NFS 服務：

```bash
sudo bash scripts/nfs/teardown_nfs_server.sh --stop-service --disable-service
```

客戶端取消掛載（按掛載點）：

```bash
sudo bash scripts/nfs/teardown_nfs_client.sh \
  --server-ip <服務端內網IP> \
  --mount-point "${JIUWEN_TEAM_WORKSPACE_ROOT:-/tmp/jiuwenclaw/shared_workspace/jiuwen_team}"
```

如果要清理該服務端在 `/etc/fstab` 的全部 nfs4 記錄：

```bash
sudo bash scripts/nfs/teardown_nfs_client.sh \
  --server-ip <服務端內網IP> \
  --clean-all-server-entries
```
