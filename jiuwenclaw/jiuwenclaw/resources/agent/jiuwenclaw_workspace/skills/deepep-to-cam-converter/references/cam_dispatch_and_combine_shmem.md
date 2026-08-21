# CAM dispatch & combine Shmem運算元
## 使用場景
提供在A3環境上執行的一對協同工作dispatch&&combine運算元，基於Shmem實現後端通訊，主要用於混合專家模型(Moe, Mixture of Experts)中用於專家並行（Expert Parallelism）帶來的動態路由問題。在如下約束下可使用
1. 執行環境為昇騰A3環境，需要支援Shmem特性
2. 當前不支援TP
3. 當前Moe場景需要滿足如下取值要求
 - 假設當前Moe通訊域的rank數定義為ep_world_size，ep_world_size只支援如下取值：[8, 16, 32, 64, 128, 144, 256, 288]
 - 假設當前Moe通訊域的專家數為num_experts，num_experts取值範圍的取值範圍是(0, 512]，由共享專家和moe專家組成共享專家數量為shared_expert_rank_num，moe專家為moe_expert_num，需要滿足(moe_expert_num + shared_expert_rank_num) ≤ 512， moe_expert_num % (ep_world_size - shared_expert_rank_num) == 0 ，moe_expert_num / (ep_world_size - shared_expert_rank_num) ≤ MAX_EXPERT_PER_RANK, 當前該值設定為32， (ep_world_size + block_num -1) / block_num ≤ MULTI_RANK_SIZE，如果shared_expert_rank_num不為0，則ep_world_size需要為其整數倍，切ep_world_size ≠ shared_expert_rank_num，(batch_size * hidden_size * ep_world_size * expert_num_per_rank * 2)小於ext_info指向的地址空間大小
4. 當前Shmem運算元使用時需要提前申請Shmem記憶體，申請Shmem記憶體時需要設定記憶體大小和ip埠引數，當前記憶體大小預設申請1024 ** 3即1GB，使用ip和埠為"tcp://127.0.0.1:8666"，注意這只是申請shmem用的引數，不要用到其他地方。
必須在運算元執行完之後(如torch.npu.synchronize())之後釋放shmem資源(aclshmem_free和aclshmem_finialize)

## 介面說明文件
當前提供運算元已提供torch擴充套件包，需要import umdk_cam_op_lib，呼叫時使用torch.ops.umdk_cam_op_lib.xxx進行呼叫
### 2.1 moe_dispatch_shmem ▶
#### 2.1.1 介面原型 
```python
moe_dispatch_shmem(
    Tensor x, 
    Tensor expert_ids, 
    Tensor scales, 
    Tensor x_active_mask, 
    int ep_world_size, 
    int ep_rank_id, 
    int moe_expert_num, 
    int tp_world_size, 
    int tp_rank_id, 
    int expert_shard_type, 
    int shared_expert_num, 
    int shared_expert_rank_num, 
    int quant_mode, 
    int global_bs, 
    int expert_token_nums_type, 
    int ext_info)
-> output: List[Tensor]
```
#### 2.1.2 介面描述 
基於SHMEM類記憶體的Dispatch介面，用以在EP通訊階段將token分發至不同的專家以供後續的操作。該介面需配合moe_combine_shmem配套使用。
#### 2.1.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(batch_size, hidden_size)|輸入Token|
|expert_ids|Tensor(int32)|必選|形狀:(batch_size, top_k)|目的專家ID資訊, 資料型別必須為int32|
|scales|Tensor|可選|非空時為float型別，存在共享專家時形狀:(m+1,h), 不存在共享專家時形狀(m, h),其中m為共享專家數|量化引數|
|x_active_mask|Tensor|可選|暫不支援，傳入None|--|
|ep_world_size|int|必選|只支援如下取值：[8, 16, 32, 64, 128, 144, 256, 288]|EP通訊域內的rank數|
|ep_rank_id|int|必選|[0, ep_world_size-1]|EP通訊域內rank ID號|
|moe_expert_num|int|必選|[1, 512]|MoE專家數|
|tp_world_size|int|必選|暫不支援，傳入1|--|
|tp_rank_id|int|必選|暫不支援，傳入0|--|
|expert_shard_type|int|必選|暫不支援，傳入0|--|
|shared_expert_num|int|必選|不支援非1的值，傳入1|每張卡上設定的共享專家數量|
|shared_expert_rank_num|int|必選|[0, ep_world_size-1]|當前moe中共享專家數量，如果不存在共享專家設定為0|
|quant_mode|int|必選|非量化傳0，量化傳2|量化模式|
|global_bs|int|必選|根據實際情況傳入，由實際記憶體大小約束|EP通訊域全域性BS大小|
|expert_token_nums_type|int|必選|傳入0：輸出每個專家處理的token數量；傳入1：輸出每個專家處理的token字首和。|輸出expert_token_nums_out的資料格式|
|ext_info|int|必選|--|SHMEM初始化後返回的基地址指標|
#### 2.1.4 返回值 
函式返回值是一個由Tensor構成的List，依次存放：expand_x, dynamic_scales, expand_idx, expert_token_nums, ep_send_count, tp_send_count和expand_scales.
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|expand_x|Tensor|當前rank是共享專家時，形狀:(rank_size * batch_size / shared_expert_num, hidden_size);當前rank是路由專家時，形狀：(expert_num_per_rank * rank_size * batch_size, hidden_size)|每個rank上所有專家的token|
|dynamic_scales|Tensor|形狀同expand_x的第一維，即:當前rank是共享專家時，形狀:(rank_size * batch_size / shared_expert_num);當前rank是路由專家時，形狀：(expert_num_per_rank * rank_size * batch_size)|量化引數資訊|
|expand_idx|Tensor|形狀：(batch_size, top_k)|在目標專家內，僅排序當前rank的token時，當前rank發出的token各自的排序ID|
|expert_token_nums|Tensor|(expert_num_on_rank)|當前rank上每個專家收到的token數|
|ep_send_count|Tensor|形狀：(expert_num_per_rank * ep_world_size)|每個專家從每個rank收到的token數|
|tp_send_count|Tensor|--|暫不支援，無意義|
|expand_scales|Tensor|--|暫不支援，無意義|
#### 2.1.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當量化模式開啟時，expand_x的資料型別為int8型別，而不開啟量化時其資料型別為bfloat16型別。
3. 當前介面不支援A2環境呼叫。
4. 當前介面不支援併發呼叫。
5. 當前介面在GE圖模式下不支援動態圖， 不支援fullgraph=true的選項。
6. 使用者應保證ext_info地址合法性。
7. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：(moe_expert_num + shared_expert_rank_num) ≤ CAM_MAX_EXPERT_NUM, 當前最大專家數為512
 - 需要滿足: moe_expert_num % (ep_world_size - shared_expert_rank_num) == 0
 - 需要滿足：moe_expert_num / (ep_world_size - shared_expert_rank_num) ≤ MAX_EXPERT_PER_RANK, 當前該值設定為32
 - 需要滿足： (ep_world_size + block_num -1) / block_num ≤ MULTI_RANK_SIZE
 - 需要滿足： 如果shared_expert_rank_num不為0，則ep_world_size需要為其整數倍，切ep_world_size ≠ shared_expert_rank_num
 - 需要滿足：(batch_size * hidden_size * ep_world_size * expert_num_per_rank * 2)小於ext_info指向的地址空間大小
- - 必須在運算元執行完之後(如torch.npu.synchronize())之後釋放shmem資源(aclshmem_free和aclshmem_finialize)

### 2.2 moe_combine_shmem ▶
#### 2.2.1 介面原型 
```python
moe_combine_shmem(
    Tensor expand_x, 
    Tensor expert_ids, 
    Tensor expand_idx, 
    Tensor ep_send_counts, 
    Tensor expert_scales, 
    Tensor tp_send_counts, 
    Tensor x_active_mask, 
    Tensor activation_scale, 
    Tensor weight_scale, 
    Tensor group_list, 
    Tensor expand_scales, 
    int ep_world_size, 
    int ep_rank_id, 
    int moe_expert_num, 
    int tp_world_size, 
    int tp_rank_id, 
    int expert_shard_type, 
    int shared_expert_num, 
    int shared_expert_rank_num, 
    int global_bs, 
    int comm_quant_mode, 
    int ext_info, 
    int out_dtype, 
    int group_list_type)
-> output: Tensor
```
#### 2.2.2 介面描述 
基於SHMEM類記憶體的Combine介面，用以在EP通訊階段將分發至不同的專家的token回合以供後續的操作。該介面需配合moe_dispatch_shmem配套使用。
#### 2.2.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|expand_x|Tensor|必選|形狀同dispatch的出參expand_x|dispatch分發至各專家上的token|
|expert_ids|Tensor(int32)|必選|形狀:(batch_size, top_k)|目的專家ID資訊, 資料型別必須為int32|
|expand_idx|Tensor|必選|形狀:(batch_size, top_k)|在目標專家內，僅排序當前rank的token時，按照rank發出的token各自的排序ID|
|ep_send_counts|Tensor|必選|形狀:(expert_num_per_rank * ep_world_size)|每個專家從每個rank收到的token數|
|expert_scales|Tensor|必選|形狀：（batch_size, top_k）|合併token時需要的權重|
|tp_send_count|Tensor|可選|暫不支援，傳入int32型別的tensor[0]即可|--|
|x_active_mask|Tensor|可選|暫不支援，傳入None|--|
|activation_scale|Tensor|可選|暫不支援，傳入None|--|
|weight_scale|Tensor|可選|暫不支援，傳入None|--|
|group_list|Tensor|可選|暫不支援，傳入None|--|
|expand_scales|Tensor|可選|暫不支援，傳入None|--|
|ep_world_size|int|必選|只支援如下取值：[8, 16, 32, 64, 128, 144, 256, 288]|EP通訊域內的rank數|
|ep_rank_id|int|必選|[0, ep_world_size-1]|EP通訊域內rank ID號|
|moe_expert_num|int|必選|[1, 512]|MoE專家數|
|tp_world_size|int|必選|暫不支援，傳入1|--|
|tp_rank_id|int|必選|暫不支援，傳入0|--|
|expert_shard_type|int|必選|暫不支援，傳入0|--|
|shared_expert_num|int|必選|不支援非1的值，傳入1|每張卡上設定的共享專家數量|
|shared_expert_rank_num|int|必選|[0, ep_world_size-1]|當前moe中共享專家數量，如果不存在共享專家設定為0|
|global_bs|int|必選|根據實際情況傳入，由實際記憶體大小約束|EP通訊域全域性BS大小|
|out_dtype|int|必選|暫不支援，傳入0|--|
|comm_quant_mode|int|必選|非量化傳0，量化傳2|量化模式|
|group_list_type|int|必選|暫不支援，傳入0|--|
|ext_info|int|必選|--|SHMEM初始化後返回的基地址指標|
#### 2.2.4 返回值 
函式返回值是一個Tensor，存放expand_x資訊。
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|expand_x|Tensor|形狀:(batch_size, hidden_size)|合併後的token資訊|
#### 2.2.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面不支援A2環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面在GE圖模式下不支援動態圖， 不支援fullgraph=true的選項。
5. 當前不支援共享專家功能。
6. 使用者應保證ext_info地址合法性。
7. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：(moe_expert_num + shared_expert_rank_num) ≤ CAM_MAX_EXPERT_NUM, 當前最大專家數為512
 - 需要滿足: moe_expert_num % (ep_world_size - shared_expert_rank_num) == 0
 - 需要滿足：moe_expert_num / (ep_world_size - shared_expert_rank_num) ≤ MAX_EXPERT_PER_RANK, 當前該值設定為32
 - 需要滿足： (ep_world_size + block_num -1) / block_num ≤ MULTI_RANK_SIZE, 
 - 需要滿足： 如果shared_expert_rank_num不為0，則ep_world_size需要為其整數倍，切ep_world_size ≠ shared_expert_rank_num
 - 需要滿足：(batch_size * hidden_size * ep_world_size * expert_num_per_rank * 2)小於ext_info指向的地址空間大小
- 必須在運算元執行完之後(如torch.npu.synchronize())之後釋放shmem資源(aclshmem_free和aclshmem_finialize)

### 示例1：deep ep dispatch & combine運算元替換為 cam dispatch & combine shmem運算元
替換前：
```python
import argparse
import os
import torch
import torch.distributed as dist
import deep_ep
import inspect

def inplace_unique(x: torch.Tensor, num_slots: int):
    assert x.dim() == 2
    mask = x < 0
    x_padded = x.masked_fill(mask, num_slots)
    bin_count = torch.zeros((x.size(0), num_slots + 1), dtype=x.dtype, device=x.device)
    bin_count.scatter_add_(1, x_padded, torch.ones_like(x_padded))
    bin_count = bin_count[:, :num_slots]
    sorted_bin_count, sorted_bin_idx = torch.sort(bin_count, dim=-1, descending=True)
    sorted_bin_idx.masked_fill_(sorted_bin_count == 0, -1)
    sorted_bin_idx = torch.sort(sorted_bin_idx, descending=True, dim=-1).values
    x[:, :].fill_(-1)
    valid_len = min(num_slots, x.size(1))
    x[:, :valid_len] = sorted_bin_idx[:, :valid_len]

def create_grouped_scores(scores: torch.Tensor, group_idx: torch.Tensor, num_groups: int):
    num_tokens, num_experts = scores.shape
    scores = scores.view(num_tokens, num_groups, -1)
    mask = torch.zeros((num_tokens, num_groups), dtype=torch.bool, device=scores.device)
    mask = mask.scatter_(1, group_idx, True).unsqueeze(-1).expand_as(scores)
    return (scores * mask).view(num_tokens, num_experts)

def init_dist(local_rank: int, num_local_ranks: int):
    ip = os.getenv('MASTER_ADDR', '127.0.0.1')
    port = int(os.getenv('MASTER_PORT', '8361'))
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    node_rank = int(os.getenv('RANK', 0))

    sig = inspect.signature(dist.init_process_group)
    params = {
        'backend': 'nccl',
        'init_method': f'tcp://{ip}:{port}',
        'world_size': num_nodes * num_local_ranks,
        'rank': node_rank * num_local_ranks + local_rank,
    }
    if 'device_id' in sig.parameters:
        params['device_id'] = torch.device(f'cuda:{local_rank}')
    dist.init_process_group(**params)
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device('cuda')
    torch.cuda.set_device(local_rank)

    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(num_local_ranks * num_nodes)))

def test_main(args, num_sms, local_rank, num_local_ranks, num_ranks, num_nodes, rank, buffer, group):
    # 配置引數
    num_tokens, hidden = args.num_tokens, args.hidden
    num_topk_groups, num_topk, num_experts = args.num_topk_groups, args.num_topk, args.num_experts

    # 準備隨機資料
    x = torch.randn((num_tokens, hidden), dtype=torch.bfloat16, device='cuda')
    scores = torch.randn((num_tokens, num_experts), dtype=torch.float32, device='cuda').abs() + 1
    
    # 計算 top-k 索引
    group_scores = scores.view(num_tokens, num_nodes, -1).amax(dim=-1)
    group_idx = torch.topk(group_scores, k=num_topk_groups, dim=-1, sorted=False).indices
    masked_scores = create_grouped_scores(scores, group_idx, num_nodes)
    topk_idx = torch.topk(masked_scores, num_topk, dim=-1, largest=True, sorted=False)[1].to(deep_ep.topk_idx_t)
    topk_weights = torch.ones((num_tokens, num_topk), dtype=torch.bfloat16, device='cuda')

    # 計算 rank 索引
    rank_idx = (topk_idx // (num_experts // num_ranks)).to(torch.int64)
    rank_idx.masked_fill_(topk_idx == -1, -1)
    inplace_unique(rank_idx, num_ranks)
    
    rdma_rank_idx = (rank_idx // num_local_ranks).to(torch.int64)
    rdma_rank_idx.masked_fill_(rank_idx == -1, -1)
    inplace_unique(rdma_rank_idx, num_nodes)

    num_tokens_per_rank, num_tokens_per_rdma_rank, num_tokens_per_expert, is_token_in_rank, _ = \
        buffer.get_dispatch_layout(topk_idx, num_experts)
    
    if local_rank == 0:
        print(f"[layout] Verified get_dispatch_layout")

    # 配置
    rdma_buffer_size, nvl_buffer_size = 128, 512
    config = deep_ep.Config(num_sms, 8, nvl_buffer_size, 16, rdma_buffer_size)

    # 測試 dispatch
    dispatch_args = {
        'x': x,
        'num_tokens_per_rank': num_tokens_per_rank,
        'num_tokens_per_rdma_rank': num_tokens_per_rdma_rank,
        'is_token_in_rank': is_token_in_rank,
        'num_tokens_per_expert': num_tokens_per_expert,
        'config': config,
        'async_finish': False,
        'topk_idx': topk_idx,
        'topk_weights': topk_weights
    }
    
    recv_x, _, handle, event = buffer.dispatch(**dispatch_args)
    event.current_stream_wait()
    
    if local_rank == 0:
        print(f"[dispatch] Completed, received {recv_x.size(0)} tokens")

    # 測試 combine
    combine_args = {
        'x': recv_x,
        'bias': (torch.ones_like(recv_x), torch.zeros_like(recv_x)),
        'handle': handle,
        'config': config,
        'async_finish': False,
        'topk_weights': topk_weights
    }
    
    combined_x, event = buffer.combine(**combine_args)
    event.current_stream_wait()
    
    if local_rank == 0:
        print(f"[combine] Completed, output shape: {combined_x.shape}")
        print("[test] All tests passed!")


def test_loop(local_rank, num_local_ranks, args):
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    rank, num_ranks, group = init_dist(local_rank, num_local_ranks)
    
    num_sms = 24
    buffer = deep_ep.Buffer(group, int(2e9), int(1e9), explicitly_destroy=True)
    
    test_main(args, num_sms, local_rank, num_local_ranks, num_ranks, num_nodes, rank, buffer, group)
    
    buffer.destroy()
    dist.barrier()
    dist.destroy_process_group()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-processes', type=int, default=8)
    parser.add_argument('--num-tokens', type=int, default=4096)
    parser.add_argument('--hidden', type=int, default=7168)
    parser.add_argument('--num-topk-groups', type=int, default=None)
    parser.add_argument('--num-topk', type=int, default=8)
    parser.add_argument('--num-experts', type=int, default=256)
    args = parser.parse_args()
    
    if args.num_topk_groups is None:
        num_nodes = int(os.getenv('WORLD_SIZE', 1))
        args.num_topk_groups = min(num_nodes, 4)

    torch.multiprocessing.spawn(test_loop, args=(args.num_processes, args), nprocs=args.num_processes)
```

替換後：
```python
import argparse
import os
import torch
import torch_npu
import torch.distributed as dist
import umdk_cam_op_lib
import shmem as shm
import inspect
import numpy as np
import random

# 關閉tls認證
shm.set_conf_store_tls(False, "")

def inplace_unique(x: torch.Tensor, num_slots: int):
    assert x.dim() == 2
    mask = x < 0
    x_padded = x.masked_fill(mask, num_slots)
    bin_count = torch.zeros((x.size(0), num_slots + 1), dtype=x.dtype, device=x.device)
    bin_count.scatter_add_(1, x_padded, torch.ones_like(x_padded))
    bin_count = bin_count[:, :num_slots]
    sorted_bin_count, sorted_bin_idx = torch.sort(bin_count, dim=-1, descending=True)
    sorted_bin_idx.masked_fill_(sorted_bin_count == 0, -1)
    sorted_bin_idx = torch.sort(sorted_bin_idx, descending=True, dim=-1).values
    x[:, :].fill_(-1)
    valid_len = min(num_slots, x.size(1))
    x[:, :valid_len] = sorted_bin_idx[:, :valid_len]

def create_grouped_scores(scores: torch.Tensor, group_idx: torch.Tensor, num_groups: int):
    num_tokens, num_experts = scores.shape
    scores = scores.view(num_tokens, num_groups, -1)
    mask = torch.zeros((num_tokens, num_groups), dtype=torch.bool, device=scores.device)
    mask = mask.scatter_(1, group_idx, True).unsqueeze(-1).expand_as(scores)
    return (scores * mask).view(num_tokens, num_experts)

def init_dist(local_rank: int, num_local_ranks: int):
    ip = os.getenv('MASTER_ADDR', '127.0.0.1')
    port = int(os.getenv('MASTER_PORT', '8361'))
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    node_rank = int(os.getenv('RANK', 0))

    sig = inspect.signature(dist.init_process_group)
    params = {
        'backend': 'hccl',
        'init_method': f'tcp://{ip}:{port}',
        'world_size': num_nodes * num_local_ranks,
        'rank': node_rank * num_local_ranks + local_rank,
    }
    if 'device_id' in sig.parameters:
        params['device_id'] = torch.device(f'npu:{local_rank}')
    dist.init_process_group(**params)
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device('npu')
    torch.npu.set_device(local_rank)

    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(num_local_ranks * num_nodes)))

def test_main(args, local_rank, num_local_ranks, num_ranks, num_nodes, rank, group):
    # 配置引數
    num_tokens, hidden = args.num_tokens, args.hidden
    num_topk_groups, num_topk, num_experts = args.num_topk_groups, args.num_topk, args.num_experts
    
    # EP通訊域配置
    ep_world_size = num_ranks
    ep_rank_id = rank
    
    shared_expert_num = 1
    shared_expert_rank_num = 0
    
    # 計算moe專家數
    moe_expert_num = num_experts - shared_expert_rank_num
    
    # 準備隨機資料
    x = torch.randn((num_tokens, hidden), dtype=torch.bfloat16, device='npu')
    scores = torch.randn((num_tokens, num_experts), dtype=torch.float32, device='npu').abs() + 1
    
    # 計算 top-k 索引（保持原有邏輯）
    group_scores = scores.view(num_tokens, num_nodes, -1).amax(dim=-1)
    group_idx = torch.topk(group_scores, k=num_topk_groups, dim=-1, sorted=False).indices
    masked_scores = create_grouped_scores(scores, group_idx, num_nodes)
    topk_idx = torch.topk(masked_scores, num_topk, dim=-1, largest=True, sorted=False)[1]
    
    # 生成 expert_ids 和 scales
    expert_ids = topk_idx.to(torch.int32)
    scales = torch.gather(masked_scores, 1, topk_idx)  # 使用分數作為權重
    
    if local_rank == 0:
        print(f"[Rank {rank}] Configuration: ep_world_size={ep_world_size}, "
              f"moe_expert_num={moe_expert_num}, "
              f"shared_expert_num={shared_expert_num}, "
              f"shared_expert_rank_num={shared_expert_rank_num}")
        print(f"[Rank {rank}] x shape: {x.shape}, expert_ids shape: {expert_ids.shape}")
    
    # SHMEM初始化
    ipPort = "tcp://127.0.0.1:8666"
    localMemSize = 1024 ** 3  # 1GB
    
    init_attrs = shm.InitAttr()
    init_attrs.my_rank = rank
    init_attrs.n_ranks = ep_world_size
    init_attrs.local_mem_size = localMemSize
    init_attrs.ip_port = ipPort
    
    shm_ret = shm.aclshmem_init(init_attrs)
    if shm_ret != 0:
        raise ValueError(f'[ERROR] shmem_init failed on rank {rank}')
    
    # 分配共享記憶體
    shmem_ptr = shm.aclshmem_malloc(localMemSize)
    
    if local_rank == 0:
        print(f"[SHMEM] Initialized, ptr: {shmem_ptr}")
    
    # 呼叫 moe_dispatch_shmem
    if local_rank == 0:
        print(f"[Dispatch] Calling moe_dispatch_shmem...")
    
    dispatch_output = torch.ops.umdk_cam_op_lib.moe_dispatch_shmem(
        x=x,
        expert_ids=expert_ids,
        scales=None,
        x_active_mask=None,
        ep_world_size=ep_world_size,
        ep_rank_id=ep_rank_id,
        moe_expert_num=moe_expert_num,
        tp_world_size=1,
        tp_rank_id=0,
        expert_shard_type=0,
        shared_expert_num=shared_expert_num,
        shared_expert_rank_num=shared_expert_rank_num,
        quant_mode=0,
        global_bs=0,
        expert_token_nums_type=0,
        ext_info=shmem_ptr
    )
    
    # 解析返回值
    expand_x = dispatch_output[0]
    dynamic_scales = dispatch_output[1]
    expand_idx = dispatch_output[2]
    expert_token_nums = dispatch_output[3]
    ep_send_count = dispatch_output[4]
    tp_send_count = dispatch_output[5]
    
    dist.barrier()

    # 準備combine引數
    x_active_mask = None
    activation_scale = None
    weight_scale = None
    group_list = None
    expand_scales = None
    out_dtype = 0
    comm_quant_mode = 0
    group_list_type = 0
    
    combined_x = torch.ops.umdk_cam_op_lib.moe_combine_shmem(
        expand_x=expand_x,
        expert_ids=expert_ids,
        expand_idx=expand_idx,
        ep_send_counts=ep_send_count,
        expert_scales=scales,
        tp_send_counts=tp_send_count,
        x_active_mask=x_active_mask,
        activation_scale=activation_scale,
        weight_scale=weight_scale,
        group_list=group_list,
        expand_scales=expand_scales,
        ep_world_size=ep_world_size,
        ep_rank_id=ep_rank_id,
        moe_expert_num=moe_expert_num,
        tp_world_size=1,
        tp_rank_id=0,
        expert_shard_type=0,
        shared_expert_num=shared_expert_num,
        shared_expert_rank_num=shared_expert_rank_num,
        global_bs=0,
        comm_quant_mode=comm_quant_mode,
        ext_info=shmem_ptr,
        out_dtype=out_dtype,
        group_list_type=group_list_type
    )
    
    torch.npu.synchronize()
    
    # 清理SHMEM
    shm.aclshmem_free(shmem_ptr)
    shm.aclshmem_finialize()


def test_loop(local_rank, num_local_ranks, args):
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    rank, num_ranks, group = init_dist(local_rank, num_local_ranks)
    
    test_main(args, local_rank, num_local_ranks, num_ranks, num_nodes, rank, group)
    
    dist.barrier()
    dist.destroy_process_group()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-processes', type=int, default=8)
    parser.add_argument('--num-tokens', type=int, default=32)
    parser.add_argument('--hidden', type=int, default=7168)
    parser.add_argument('--num-topk-groups', type=int, default=None)
    parser.add_argument('--num-topk', type=int, default=4)
    parser.add_argument('--num-experts', type=int, default=8)
    args = parser.parse_args()
    
    if args.num_topk_groups is None:
        num_nodes = int(os.getenv('WORLD_SIZE', 1))
        args.num_topk_groups = min(num_nodes, 4)
    
    torch.multiprocessing.spawn(test_loop, args=(args.num_processes, args), nprocs=args.num_processes)
```