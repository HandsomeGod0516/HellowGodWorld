# CAM dispatch & combine A2運算元
## 使用場景
提供在A2環境上執行的一對協同工作dispatch&&combine運算元，主要用於混合專家模型(Moe, Mixture of Experts)中用於專家並行（Expert Parallelism）帶來的動態路由問題。在如下約束下可使用
1. 執行環境為昇騰A2環境
2. 當前Moe場景中不存在共享專家
3. 當前Moe場景需要滿足如下取值要求
 - Moe會選擇機率最高的K個專家，將token透過dispatch運算元分發給對應的專家並透過combine運算元收回，當前這套運算元需要保證這個top_k取值範圍為(2， 16]
 - 假設當前Moe通訊域的rank數定義為num_ranks，當前僅支援num_ranks為16
 - 假設當前Moe通訊域的專家數為num_experts，num_experts取值範圍的取值範圍是(0, 256]，並且需要滿足num_experts % num_ranks ==0
 - 假設當前本卡要傳送的token形狀為(batch_size, hidden_size), batch_size的取值範圍需要滿足[1, 4K], hidden_size的取值範圍需要滿足(0， 7168]且(hidden_size % 32) == 0
 - 在進行dispatch & combine過程中batch_size的取值範圍需要滿足[1, 4K]
 - 當前不支援開啟量化


## 介面說明
當前提供運算元已提供torch擴充套件包，需要import umdk_cam_op_lib，呼叫時使用torch.ops.umdk_cam_op_lib.xxx進行呼叫
### 2.1 get_dispatch_layout_a2 ▶
#### 2.1.1 介面原型 
```python
get_dispatch_layout_a2(
    Tensor topk_idx, 
    int num_experts, 
    int num_ranks)
-> output: tuple(Tensor, Tensor)
```
#### 2.1.2 介面描述
A2代際Prefill階段Dispatch使用的前置介面，用以在當前rank將Token拓展TopK份(存在共享專家時，此處為(topK+1)份)之後按照專家粒度重排，方便後續的分發操作。該介面需配合moe_dispatch_prefill_a2和moe_combine_prefill_a2使用。
#### 2.1.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， int64型別，取值範圍：[0, num_experts)|目標專家的ID資訊|
|num_experts|int|必選|取值範圍：(0, 256]|MOE專家數|
|num_ranks|int|必選|當前僅支援16|EP通訊域rank數|
#### 2.1.4 返回值 
函式返回值是一個2個Tensor構成的Tuple，分別存放：number_tokens_per_expert和notify_send_data.
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|number_tokens_per_expert|Tensor|形狀：（num_experts）,int型別|當前rank上傳送給每個專家的token個數|
|notify_send_data|Tensor|形狀：(num_experts * EXPERT_DATA_SIZE + server_num + max_bs * (1 + 2* server_num + num_experts)), 資料型別為int。當前EXPERT_DATA_SIZE=4097，max_bs=4096。七個部分的形狀資訊：<br> 1. num_tokens_per_expert, 形狀：（num_experts）；<br> 2. num_token_per_server_uniq, 形狀：（num_experts）；<br> 3. num_each_token_to_server, 形狀：（max_bs * num_server）;<br> 4. each_token_to_num_server, 形狀：（max_bs）;<br> 5. each_token_offset_to_server, 形狀：（max_bs * num_server）；<br> 6. send_token_idx, 形狀：（max_bs * num_experts）；<br> 7. expert_rank_token_idx, 形狀：（num_experts， max_bs）；<br> |由七個部分組成的tensor,分別表示<br> 1. 每個expert從本卡收到的token數目；<br> 2. 每個server從本卡接收到的token數目（去重）；<br> 3. 本卡每個token發往每個server的個數；<br> 4. 本卡每個token發往的server個數；<br> 5. 本卡每個token發往每個server,token的順序偏移。<br> 6. 本卡每個token按照專家維度分桶，在桶中的序號偏移。<br> 7. 每個專家收到的每個token，其對應的each_token_offset_to_server值|
#### 2.1.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A2環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面不支援入圖使用。
5. 當前介面不支援共享專家。
6. 除滿足上述形狀約束外，其他引數取值要求：
 - top_k取值範圍：(2， 16]
 - 需要滿足: num_experts % num_ranks == 0
 - 需要滿足: num_ranks % 8 == 0
 - 需要配置：export HCCL_INTRA_PCIE_ENABLE = 1, export HCCL_INTRA_ROCE_ENABLE = 0

### 2.2 moe_dispatch_prefill_a2 ▶
#### 2.2.1 介面原型 
```python
moe_dispatch_prefill_a2(
    Tensor x, 
    Tensor topk_idx, 
    Tensor topk_weights, 
    Tensor num_tokens_per_expert,
 	Tensor notify_send_data, 
    str group_ep, 
    int rank, 
    int num_ranks, 
    bool use_quant) 
-> output: Tensor[]
```
#### 2.2.2 介面描述 
A2代際Prefill階段Dispatch介面，將Token按照topk_idx的規則傳送給對應專家。
#### 2.2.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(batch_size, hidden_size), 支援bf16, float16型別|本卡傳送的token|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， 資料型別為int64，取值範圍[0, num_experts)|每個token的目標專家ID資訊|
|topk_weights|Tensor|必選|形狀:(batch_size, topk)， 資料型別為float32|每個token的topk個目標專家的權重資訊|
|number_tokens_per_expert|Tensor|必選|形狀：（num_experts），資料型別為int|當前rank上傳送給每個專家的token個數|
|notify_send_data|Tensor|必選|形狀：(num_experts * EXPERT_DATA_SIZE + server_num + max_bs * (1 + 2* server_num + num_experts)), 資料型別為int|get_dispatch_layout_a2的輸出，含義參考該部分的描述|
|group_ep|str|必選|--|HCCL通訊域名稱|
|rank|int|必選|[0, num_ranks)|本卡在通訊域中的rankID|
|num_ranks|int|必選|當前只支援16|EP通訊域rank數|
|use_quant|bool|必選|False: 不開啟量化, 當前版本暫不支援量化|Dispatch量化指示符|
#### 2.2.4 返回值 
函式返回值是一個8個Tensor構成的List，分別存放：recv_x, dynamic_scales_out, expand_idx_out, ep_rank_token_cnt, offset_inner, offset_outer, count_outer, expand_scales.
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|recv_x|Tensor|形狀：(recv_token_num, hidden_size), 其中recv_token_num為本卡收到的token個數。當use_quant為true時，資料型別為int8, false時資料型別與入參x一致。|當前rank上收到的token資訊|
|dynamic_scales_out|Tensor|形狀：(recv_token_num), 資料型別為float.當use_quant為false時該值沒有意義。|當前rank上收到token的動態量化scale資訊|
|expand_idx_out|Tensor|形狀：(maxbs, num_experts), 資料型別為int|本卡發出的token在同一專家內的序號|
|ep_rank_token_cnt|Tensor|形狀：(num_experts, num_ranks), 資料型別為int|每個專家從不同rank接收的token數量|
|offset_inner|Tensor|形狀：(2, max_bs, num_experts), 資料型別為int|token給對應專家的偏移，僅存放當前卡對端server的同號卡資訊|
|offset_outer|Tensor|形狀：(max_bs, num_experts), 資料型別為int|token傳送給對應server的token序號|
|count_outer|Tensor|形狀：(max_bs), 資料型別為int|token傳送到server的數量|
|expand_scales|Tensor|形狀：(num_recv_tokens), 資料型別為float|接收token時對應到topk_weights中的權重|
#### 2.2.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A2環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面不支援入圖使用。
5. 當前介面不支援共享專家。
6. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：BS取值範圍[1, 4K]
 - 需要滿足: num_experts取值範圍(0, 256]
 - 需要滿足: topk取值範圍(2, 16]
 - 需要滿足: (num_experts % num_ranks) == 0
 - 需要滿足: (num_ranks % 8) == 0
 - 需要滿足: hidden_size取值範圍(0， 7168]且(hidden_size % 32) == 0
 - 需要滿足: 配置全域性宏HCCL_BUFFERSIZE=4096
 - 需要配置：export HCCL_INTRA_PCIE_ENABLE = 1, export HCCL_INTRA_ROCE_ENABLE = 0

### 2.3 moe_combine_prefill_a2 ▶
#### 2.3.1 介面原型 
```python
moe_combine_prefill_a2(
    Tensor x, 
    Tensor topk_idx, 
    Tensor topk_weights, 
    Tensor src_idx, 
    Tensor send_head, 
    Tensor expand_scales, 
    Tensor offset_inner, 
    Tensor offset_outer, 
    Tensor count_outer, 
    str group_ep, 
    int rank, 
    int num_ranks)
-> output: Tensor
```
#### 2.3.2 介面描述 
![moe_combine_prefill_a2示意圖](figures/moe_combine_prefill_a2.png)
A2代際Prefill階段Combine介面，將按照topk_idx的規則傳送給對應專家的token，按照topk_weights指定的權重收回。
#### 2.3.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(recv_token_num, hidden_size), 支援bf16, float16型別|本卡dispatch階段收集到的token|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， 資料型別為int64, 取值範圍[0, num_experts)|每個token的目標專家ID資訊|
|topk_weights|Tensor|必選|形狀:(batch_size, topk)， 資料型別為float32|每個token的topk個目標專家的權重資訊|
|src_idx|Tensor|必選|形狀：(max_bs, num_experts), 資料型別為int|對應moe_dispatch_prefill_a2的出參expand_idx_out|
|send_head|Tensor|必選|形狀：(num_experts), 資料型別為int|對應moe_dispatch_prefill_a2的出參ep_rank_token_cnt|
|expand_scales|Tensor|必選|形狀：(num_recv_tokens), 資料型別為float|對應moe_dispatch_prefill_a2的出參expand_scales|
|offset_inner|Tensor|必選|形狀：(2, max_bs, num_experts), 資料型別為int|對應moe_dispatch_prefill_a2的出參offset_inner|
|offset_outer|Tensor|必選|形狀：(max_bs, num_experts), 資料型別為int|對應moe_dispatch_prefill_a2的出參offset_outer|
|count_outer|Tensor|形狀：(max_bs), 資料型別為int|對應moe_dispatch_prefill_a2的出參count_outer|
|group_ep|str|必選|--|HCCL通訊域名稱|
|rank|int|必選|[0, num_ranks)|本卡在通訊域中的rankID|
|num_ranks|int|必選|當前只支援16|EP通訊域rank數|
#### 2.3.4 返回值 
函式返回值是一個Tensor，存放combine_x資訊。
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|combine_x|Tensor|形狀：(batch_size, hidden_size)。資料型別與x一致|當前rank上收到的token資訊。|
#### 2.3.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A2環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面不支援入圖使用。
5. 當前介面不支援共享專家。
6. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：BS取值範圍[1, 4K]
 - 需要滿足: num_experts取值範圍(0, 256]
 - 需要滿足: topk取值範圍[2, 16]
 - 需要滿足: (num_experts % num_ranks) == 0
 - 需要滿足: hidden_size取值範圍(0， 7168]且(hidden_size % 32) == 0
 - 需要滿足: 配置全域性宏HCCL_BUFFERSIZE=4096
 - 需要配置：export HCCL_INTRA_PCIE_ENABLE = 1, export HCCL_INTRA_ROCE_ENABLE = 0

## 替換示例
### 示例1：deep ep dispatch & combine運算元替換為 cam dispatch & combine a2運算元
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
import torch.distributed as dist
import torch_npu
import umdk_cam_op_lib


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

    torch.npu.set_device(local_rank)

    dist.init_process_group(
        backend='hccl',
        init_method=f'tcp://{ip}:{port}',
        world_size=num_nodes * num_local_ranks,
        rank=node_rank * num_local_ranks + local_rank,
    )
    
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'npu:{local_rank}')
    
    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(num_local_ranks * num_nodes)))


def test_main(args, local_rank, num_local_ranks, num_ranks, num_nodes, rank, group):
    # 配置引數
    num_tokens, hidden = args.num_tokens, args.hidden
    num_topk_groups, num_topk, num_experts = args.num_topk_groups, args.num_topk, args.num_experts
    
    # 準備隨機資料
    x = torch.randn((num_tokens, hidden), dtype=torch.bfloat16, device='npu')
    scores = torch.randn((num_tokens, num_experts), dtype=torch.float32, device='npu').abs() + 1
    
    # 計算 top-k 索引
    group_scores = scores.view(num_tokens, num_nodes, -1).amax(dim=-1)
    group_idx = torch.topk(group_scores, k=num_topk_groups, dim=-1, sorted=False).indices
    masked_scores = create_grouped_scores(scores, group_idx, num_nodes)
    topk_idx = torch.topk(masked_scores, num_topk, dim=-1, largest=True, sorted=False)[1].to(torch.int64)
    topk_weights = torch.ones((num_tokens, num_topk), dtype=torch.float32, device='npu')
    # 計算 rank 索引
    rank_idx = (topk_idx // (num_experts // num_ranks)).to(torch.int64)
    rank_idx.masked_fill_(topk_idx == -1, -1)
    inplace_unique(rank_idx, num_ranks)
    
    # 使用新的 get_dispatch_layout_a2 運算元
    num_tokens_per_expert, notify_send_data = torch.ops.umdk_cam_op_lib.get_dispatch_layout_a2(
        topk_idx, num_experts, num_ranks
    )
    
    if local_rank == 0:
        print(f"[layout] Verified get_dispatch_layout_a2")

    # 使用新的 moe_dispatch_prefill_a2 運算元
    use_quant = False  # 當前不支援量化
    ep_hcomm_info = group._get_backend(torch.device('npu')).get_hccl_comm_name(rank)
    ep_hcomm_info = ep_hcomm_info.encode('utf-8')
    
    dispatch_args = {
        'x': x,
        'topk_idx': topk_idx,
        'topk_weights': topk_weights,
        'num_tokens_per_expert': num_tokens_per_expert,
        'notify_send_data': notify_send_data,
        'group_ep': ep_hcomm_info,
        'rank': rank,
        'num_ranks': num_ranks,
        'use_quant': use_quant,
    }
    (
        recv_x,
        dynamic_scales_out,
        expand_idx_out,
        recv_count,
        offset_inner,
        offset_outer,
        count_outer,
        expand_scales,
    ) = torch.ops.umdk_cam_op_lib.moe_dispatch_prefill_a2(**dispatch_args)
    
    dist.barrier()
    
    if local_rank == 0:
        print(f"[dispatch] Completed using moe_dispatch_prefill_a2")

    combine_args = {
        'x': recv_x,
        'topk_idx': topk_idx,
        'topk_weights': topk_weights,
        'src_idx': expand_idx_out,
        'send_head': recv_count,
        'expand_scales': expand_scales,
        'offset_inner': offset_inner,
        'offset_outer': offset_outer,
        'count_outer': count_outer,
        'group_ep': ep_hcomm_info,
        'rank': rank,
        'num_ranks': num_ranks,
    }
    
    combined_x = torch.ops.umdk_cam_op_lib.moe_combine_prefill_a2(**combine_args)
    
    dist.barrier()
    
    if local_rank == 0:
        print(f"[combine] Completed using moe_combine_prefill_a2")


def test_loop(local_rank, num_local_ranks, args):
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    rank, num_ranks, group = init_dist(local_rank, num_local_ranks)
    
    test_main(args, local_rank, num_local_ranks, num_ranks, num_nodes, rank, group)
    
    dist.barrier()
    dist.destroy_process_group()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-processes', type=int, default=16, help="Must be 16 for A2 environment")
    parser.add_argument('--num-tokens', type=int, default=4096, help="Batch size, must be in [1, 4096]")
    parser.add_argument('--hidden', type=int, default=7168, help="Hidden size, must be divisible by 32 and <= 7168")
    parser.add_argument('--num-topk-groups', type=int, default=None)
    parser.add_argument('--num-topk', type=int, default=8, help="TopK value, must be in (2, 16]")
    parser.add_argument('--num-experts', type=int, default=256, help="Number of experts, must be <= 256 and divisible by num_ranks")
    args = parser.parse_args()
    
    if args.num_topk_groups is None:
        num_nodes = int(os.getenv('WORLD_SIZE', 1))
        args.num_topk_groups = min(num_nodes, 4)
    
    torch.multiprocessing.spawn(test_loop, args=(args.num_processes, args), nprocs=args.num_processes)
```