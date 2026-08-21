# NCCL到HCCL通訊域轉換參考指南

## 概述
本文件提供了將NCCL通訊域轉換為昇騰NPU HCCL通訊域的常見模式參考，幫助大模型在CAM運算元替換過程中正確處理通訊域的轉換。

## 基本概念對比

### NCCL (NVIDIA Collective Communications Library)
- **平臺**: NVIDIA GPU
- **通訊後端**: NVLink, PCIe, InfiniBand
- **主要API**: `ncclComm_t`, `ncclGroupStart()`, `ncclGroupEnd()`
- **資料型別**: 基於CUDA的`cudaStream_t`

### HCCL (Huawei Collective Communications Library)
- **平臺**: 昇騰NPU (Ascend)
- **通訊後端**: PCIe, RoCE, HCCS
- **主要API**: `HcclComm`, `HcclGroupStart()`, `HcclGroupEnd()`
- **資料型別**: 基於NPU的`aclrtStream`

## 轉換示例

### 模式1：通訊域初始化

#### NCCL版本
```python
import os
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP

def train_with_hccl():
    # 1. 初始化分散式環境
    rank = int(os.environ['RANK'])
    world_size = int(os.environ['WORLD_SIZE'])
    local_rank = int(os.environ['LOCAL_RANK'])

    # 2. 初始化程序組，指定後端為 'hccl'
    dist.init_process_group(backend='hccl', init_method='env://')

    # 3. 設定當前程序繫結的 NPU 裝置
    torch.npu.set_device(local_rank)
    device = torch.device('npu', local_rank)

    # 4. 定義一個簡單的模型
    model = torch.nn.Linear(10, 10).to(device)
    
    # 5. 使用 DDP 包裝模型
    # DDP 會自動使用 HCCL 後端進行梯度同步
    ddp_model = DDP(model, device_ids=[local_rank])

    # --- 訓練迴圈 ---
    # 在訓練迴圈中，ddp_model 的 backward() 會自動觸發 HCCL AllReduce 操作
    # ----------------

    # 訓練結束後，清理資源
    dist.destroy_process_group()

if __name__ == '__main__':
    # 同樣，使用 multiprocessing 來模擬
    import torch.multiprocessing as mp
    # 假設單機有 8 張 NPU
    world_size = 8
    mp.spawn(train_with_hccl, args=(), nprocs=world_size, join=True)
```

#### HCCL版本
```python
import os
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP

def train_with_hccl():
    # 1. 初始化分散式環境
    rank = int(os.environ['RANK'])
    world_size = int(os.environ['WORLD_SIZE'])
    local_rank = int(os.environ['LOCAL_RANK'])
    
    # HCCL 通常需要 RANK_TABLE_FILE 環境變數來定位叢集配置檔案
    # os.environ['RANK_TABLE_FILE'] = '/path/to/hccl_rank_table.json'

    # 2. 初始化程序組，指定後端為 'hccl'
    dist.init_process_group(backend='hccl', init_method='env://')

    # 3. 設定當前程序繫結的 NPU 裝置
    # 注意：這裡使用 torch.npu 而非 torch.cuda
    torch.npu.set_device(local_rank)
    device = torch.device('npu', local_rank)

    # 4. 定義一個簡單的模型
    model = torch.nn.Linear(10, 10).to(device)
    
    # 5. 使用 DDP 包裝模型
    # DDP 會自動使用 HCCL 後端進行梯度同步
    ddp_model = DDP(model, device_ids=[local_rank])

    # --- 訓練迴圈 ---
    # 在訓練迴圈中，ddp_model 的 backward() 會自動觸發 HCCL AllReduce 操作
    # ----------------

    # 訓練結束後，清理資源
    dist.destroy_process_group()

if __name__ == '__main__':
    # 同樣，使用 multiprocessing 來模擬
    import torch.multiprocessing as mp
    # 假設單機有 8 張 NPU
    world_size = 8
    mp.spawn(train_with_hccl, args=(), nprocs=world_size, join=True)
```

### 模式2：集體通訊操作

#### 2.1 AllReduce操作

**NCCL版本**:
```python
# PyTorch NCCL AllReduce
dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
```

**HCCL版本**:
```python
# PyTorch NPU HCCL AllReduce
dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
# 注意：在NPU上需要確保tensor在NPU裝置上
```

#### 2.2 AllGather操作

**NCCL版本**:
```python
# PyTorch NCCL AllGather
output_tensor_list = [torch.empty_like(tensor) for _ in range(world_size)]
dist.all_gather(output_tensor_list, tensor)
```

**HCCL版本**:
```python
# PyTorch NPU HCCL AllGather
output_tensor_list = [torch.empty_like(tensor) for _ in range(world_size)]
dist.all_gather(output_tensor_list, tensor)
```

#### 2.3 Broadcast操作

**NCCL版本**:
```python
# PyTorch NCCL Broadcast
dist.broadcast(tensor, src=rank)
```

**HCCL版本**:
```python
# PyTorch NPU HCCL Broadcast
dist.broadcast(tensor, src=rank)
```

### 模式3：點對點通訊

#### NCCL版本
```python
# PyTorch NCCL點對點
dist.send(tensor, dst=dest_rank)
dist.recv(tensor, src=src_rank)
```

#### HCCL版本
```python
# PyTorch NPU HCCL點對點
dist.send(tensor, dst=dest_rank)
dist.recv(tensor, src=src_rank)
```

## 環境變數轉換

### NCCL環境變數
```bash
# NCCL典型配置
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0
export NCCL_IB_DISABLE=0
export NCCL_P2P_DISABLE=0
```

### HCCL環境變數
```bash
# HCCL對應配置
export HCCL_DEBUG=INFO
export HCCL_SOCKET_IFNAME=eth0
export HCCL_IB_DISABLE=0
export HCCL_P2P_DISABLE=0

# HCCL特有配置
export HCCL_BUFFERSIZE=4096  # A3環境需要
export HCCL_INTRA_PCIE_ENABLE=1  # A2環境需要
export HCCL_INTRA_ROCE_ENABLE=0  # A2環境需要
```

## 資料型別對映

| NCCL資料型別 | HCCL資料型別 | 說明 |
|-------------|-------------|------|
| `ncclInt8` | `HcclInt8` | 8位整數 |
| `ncclInt32` | `HcclInt32` | 32位整數 |
| `ncclFloat16` | `HcclFloat16` | 半精度浮點 |
| `ncclFloat32` | `HcclFloat32` | 單精度浮點 |
| `ncclBfloat16` | `HcclBfloat16` | Brain浮點16 |

## 操作型別對映

| NCCL操作 | HCCL操作 | 說明 |
|---------|---------|------|
| `ncclSum` | `HcclSum` | 求和 |
| `ncclProd` | `HcclProd` | 乘積 |
| `ncclMax` | `HcclMax` | 最大值 |
| `ncclMin` | `HcclMin` | 最小值 |
| `ncclAvg` | `HcclAvg` | 平均值 |

## 錯誤處理轉換

### NCCL錯誤處理
```c
ncclResult_t result = ncclAllReduce(...);
if (result != ncclSuccess) {
    printf("NCCL error: %s\n", ncclGetErrorString(result));
    // 處理錯誤
}
```

### HCCL錯誤處理
```c
HcclResult result = HcclAllReduce(...);
if (result != HCCL_SUCCESS) {
    printf("HCCL error: %d\n", result);
    // 處理錯誤
}
```

## PyTorch分散式API轉換

### 通用轉換模式
```python
# NCCL版本
import torch.distributed as dist

# 初始化
dist.init_process_group(backend='nccl', ...)

# 通訊操作
dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
dist.all_gather(output_list, tensor)
dist.broadcast(tensor, src=0)

# HCCL版本（僅需修改backend）
import torch.distributed as dist

# 初始化
dist.init_process_group(backend='hccl', ...)  # 僅此修改

# 通訊操作（API保持不變）
dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
dist.all_gather(output_list, tensor)
dist.broadcast(tensor, src=0)
```

## 效能最佳化建議

### 1. 緩衝區管理
- **NCCL**: 使用`cudaMalloc`分配GPU視訊記憶體
- **HCCL**: 使用`aclrtMalloc`分配NPU記憶體

### 2. 流同步
- **NCCL**: 使用`cudaStreamSynchronize(stream)`
- **HCCL**: 使用`aclrtSynchronizeStream(stream)`

### 3. 通訊重疊
- **NCCL**: 使用多個CUDA流重疊通訊和計算
- **HCCL**: 使用多個ACL流重疊通訊和計算

### 4. 拓撲感知
- **NCCL**: 使用`ncclTopoGetSystem`獲取系統拓撲
- **HCCL**: 使用`HcclGetTopoInfo`獲取NPU拓撲

## 常見問題與解決方案

### 問題1：通訊域初始化失敗
**NCCL原因**: `ncclCommInitRank`返回`ncclInvalidArgument`
**HCCL對應**: `HcclCommInitRank`返回`HCCL_INVALID_ARGUMENT`
**解決方案**: 檢查`world_size`和`rank`引數是否有效

### 問題2：資料型別不匹配
**NCCL表現**: `ncclAllReduce`返回`ncclInvalidType`
**HCCL表現**: `HcclAllReduce`返回`HCCL_INVALID_TYPE`
**解決方案**: 確保傳送和接收緩衝區資料型別一致

### 問題3：緩衝區大小不匹配
**NCCL表現**: `ncclAllGather`返回`ncclInvalidUsage`
**HCCL表現**: `HcclAllGather`返回`HCCL_INVALID_USAGE`
**解決方案**: 檢查`sendcount`和`recvcount`引數

## 總結
將NCCL通訊域轉換為HCCL通訊域主要涉及：
1. **後端修改**: `nccl` → `hccl`
2. **裝置修改**: `.cuda()` → `.npu()`
3. **環境變數**: 更新為HCCL特定配置
4. **運算元替換**: 將複雜NCCL通訊替換為CAM融合運算元
5. **效能最佳化**: 利用NPU特性和CAM運算元最佳化

透過以上轉換，可以充分利用昇騰NPU的硬體特性，實現更高效的分散式訓練和推理。