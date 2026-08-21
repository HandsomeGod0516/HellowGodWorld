# CAM dispatch & combine A3運算元
## 使用場景
提供在A3環境上執行的一對協同工作dispatch&&combine運算元，主要用於混合專家模型(Moe, Mixture of Experts)中用於專家並行（Expert Parallelism）帶來的動態路由問題。在如下約束下可使用
1. 執行環境為昇騰A3環境
2. 當前Moe場景中不存在共享專家
3. 當前Moe場景需要滿足如下取值要求
 - Moe會選擇機率最高的K個專家，將token透過dispatch運算元分發給對應的專家並透過combine運算元收回，當前這套運算元需要保證這個top_k取值範圍為(0， 16]
 - 假設當前Moe通訊域的rank數定義為num_ranks，num_ranks取值範圍為[2, 384]
 - 假設當前Moe通訊域的專家數為num_experts，num_experts取值範圍的取值範圍是(0, 512]，並且需要滿足num_experts % num_ranks ==0 和 num_experts >= num_ranks
 - 假設當前本卡要傳送的token形狀為(batch_size, hidden_size), batch_size的取值範圍需要滿足(0, 8000], hidden_size的取值範圍需要滿足(0， 7168]且(hidden_size % 32) == 0
 - 在進行dispatch & combine過程中batch_size的取值範圍需要滿足[1, 4K]

## api說明
當前提供運算元已提供torch擴充套件包，需要import umdk_cam_op_lib，呼叫時使用torch.ops.umdk_cam_op_lib.xxx進行呼叫
### 2.1 get_dispatch_layout ▶
#### 2.1.1 介面原型 
```python
get_dispatch_layout(
    Tensor topk_idx, 
    int num_experts, 
    int num_ranks)
-> output: tuple(Tensor, Tensor)
```
#### 2.1.2 介面描述 
A3代際Prefill階段Dispatch使用的前置介面，用以在當前rank將Token拓展TopK份之後按照專家粒度重排，方便後續的分發操作。該介面需配合moe_dispatch_prefill和moe_combine_prefill使用。
#### 2.1.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， int64型別|目標專家的ID資訊|
|num_experts|int|必選|取值範圍：(0, 512]|MOE專家數|
|num_ranks|int|必選|取值範圍：[1, 384]|EP通訊域rank數|
#### 2.1.4 返回值 
函式返回值是一個2個Tensor構成的Tuple，分別存放：number_tokens_per_expert和send_token_idx.
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|number_tokens_per_expert|Tensor|形狀：（num_experts）|當前rank上傳送給每個專家的token個數|
|send_token_idx|Tensor|形狀：(batch_size, top_k)|當前rank上，傳送給每個專家的token在以專家重排分桶後，其在桶裡的第幾個位置|
#### 2.1.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A3環境呼叫。
4. 當前介面不支援併發呼叫。
5. 當前介面不支援入圖使用。
6. 除滿足上述形狀約束外，其他引數取值要求：
 - top_k取值範圍：(0， 16]
 - batch_size取值範圍：(0, 8000]
 - 需要滿足: num_experts % num_ranks == 0
 - 需要滿足：num_experts >= num_ranks

### 2.2 moe_dispatch_prefill ▶
#### 2.2.1 介面原型 
```python
moe_dispatch_prefill(
    Tensor x, 
    Tensor topk_idx, 
    Tensor topk_weights, 
    Tensor num_tokens_per_expert, 
    Tensor send_token_idx_small, 
    str group_ep, 
    int rank, 
    int num_ranks, 
    bool use_quant) 
-> output: tuple(Tensor, Tensor, Tensor, Tensor, Tensor)
```
#### 2.2.2 介面描述 
A3代際Prefill階段Dispatch介面，將Token按照topk_idx的規則傳送給對應專家。
#### 2.2.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(batch_size, hidden_size), 支援bf16, float16型別|本卡傳送的token|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， 資料型別為int64|每個token的目標專家ID資訊|
|topk_weights|Tensor|必選|形狀:(batch_size, topk)， 資料型別為float32|每個token的topk個目標專家的權重資訊|
|number_tokens_per_expert|Tensor|必選|形狀：（num_experts），資料型別為int|當前rank上傳送給每個專家的token個數|
|send_token_idx_small|Tensor|必選|形狀：(batch_size, top_k), 資料型別為int|當前rank上，傳送給每個專家的token在以專家重排分桶後，其在桶裡的第幾個位置|
|group_ep|str|必選|--|HCCL通訊域名稱|
|rank|int|必選|[0, num_ranks)|本卡在通訊域中的rankID|
|num_ranks|int|必選|[2, 384]|EP通訊域rank數|
|use_quant|bool|必選|True: 開啟量化； False: 關閉量化|Dispatch量化指示符|
#### 2.2.4 返回值 
函式返回值是一個5個Tensor構成的Tuple，分別存放：recv_x, dynamic_scales_out, expand_idx_out, recv_count, recv_token_per_expert.
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|recv_x|Tensor|形狀：(recv_token_num, hidden_size), 其中recv_token_num為本卡收到的token個數。當use_quant為true時，資料型別為int8, false時資料型別與入參x一致|當前rank上收到的token資訊|
|dynamic_scales_out|Tensor|形狀：(recv_token_num), 資料型別為float.當use_quant為false時該值沒有意義。|當前rank上收到token的動態量化scale資訊|
|expand_idx_out|Tensor|形狀：(recv_token_num * 3), 資料型別為int|本卡收到的token資訊三元組，每組三個數的含義依次為：token的源rank, token在源rank的序號（BS視角），token在源rank時topk專家擴充套件重排後的序號（專家視角）|
|recv_count|Tensor|形狀：(num_experts), 資料型別為int|當前rank上每個專家從每個rank收到的收到token數，為字首和|
|recv_tokens_per_expert|Tensor|形狀：(local_expert_num), 資料型別為int64|當前rank上每個專家收到的token資訊|
#### 2.2.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A3環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面不支援入圖使用。
5. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：BS取值範圍[1, 8K]
 - 需要滿足: num_ranks取值範圍[2, 384]
 - 需要滿足: num_experts取值範圍(0, 512]
 - 需要滿足: topk取值範圍(0, 16]
 - 需要滿足: (num_experts % num_ranks) == 0
 - 需要滿足: 配置全域性宏HCCL_BUFFERSIZE=4096
 - 需要滿足：num_experts >= num_ranks

### 2.3 moe_combine_prefill ▶
#### 2.3.1 介面原型 
```python
moe_combine_prefill(
    Tensor x, 
    Tensor topk_idx, 
    Tensor topk_weights, 
    Tensor src_idx, 
    Tensor send_head,
    str group_ep, 
    int rank, 
    int num_ranks) 
-> output: Tensor
```
#### 2.3.2 介面描述 
A3代際Prefill階段Combine介面，將按照topk_idx的規則傳送給對應專家的token，按照topk_weights指定的權重收回。
#### 2.3.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(recv_token_num, hidden_size), 支援bf16, float16型別|本卡dispatch階段收集到的token|
|topk_idx|Tensor|必選|形狀:(batch_size, topk)， 資料型別為int64|每個token的目標專家ID資訊|
|topk_weights|Tensor|必選|形狀:(batch_size, topk)， 資料型別為float32|每個token的topk個目標專家的權重資訊|
|src_idx|Tensor|形狀：(recv_token_num * 3), 資料型別為int|本卡收到的token資訊三元組，每組三個數的含義依次為：token的源rank, token在源rank的序號（BS視角），token在源rank時topk專家擴充套件重排後的序號（專家視角）。對應moe_dispatch_prefill的出參expand_idx_out|
|send_head|Tensor|形狀：(num_experts), 資料型別為int|當前rank上每個專家從每個rank收到的收到token的動態量化scale資訊，該資訊按照一維排開。對應moe_dispatch_prefill的出參recv_count|
|group_ep|str|必選|--|HCCL通訊域名稱|
|rank|int|必選|[0, num_ranks)|本卡在通訊域中的rankID|
|num_ranks|int|必選|[2, 384]|EP通訊域rank數|
#### 2.3.4 返回值 
函式返回值是一個Tensor，存放combine_x資訊。
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|combine_x|Tensor|形狀：(batch_size, hidden_size)。資料型別與x一致|當前rank上收到的token資訊|
#### 2.3.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A3環境呼叫。
3. 當前介面不支援併發呼叫。
4. 當前介面不支援入圖使用。
5. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：BS取值範圍[1, 8K]
 - 需要滿足: num_ranks取值範圍[2, 384]
 - 需要滿足: num_experts取值範圍(0, 512]
 - 需要滿足: topk取值範圍(0, 16]
 - 需要滿足: (num_experts % num_ranks) == 0
 - 需要滿足: 配置全域性宏HCCL_BUFFERSIZE=4096
 - 需要滿足：num_experts >= num_ranks

## 替換示例
### 示例1：deep ep dispatch & combine運算元替換為 cam dispatch & combine a3運算元
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

    rank_idx = (topk_idx // (num_experts // num_ranks)).to(torch.int64)
    rank_idx.masked_fill_(topk_idx == -1, -1)
    inplace_unique(rank_idx, num_ranks)
    
    # 使用新的 get_dispatch_layout 運算元
    num_tokens_per_expert, send_token_idx = torch.ops.umdk_cam_op_lib.get_dispatch_layout(
        topk_idx, num_experts, num_ranks
    )
    
    if local_rank == 0:
        print(f"[layout] Verified get_dispatch_layout")

    # 使用新的 moe_dispatch_prefill 運算元
    use_quant = False
    ep_hcomm_info = group._get_backend(torch.device('npu')).get_hccl_comm_name(rank)
    ep_hcomm_info = ep_hcomm_info.encode('utf-8')
    
    dispatch_args = {
        'x': x,
        'topk_idx': topk_idx,
        'topk_weights': topk_weights,
        'num_tokens_per_expert': num_tokens_per_expert,
        'send_token_idx_small': send_token_idx,
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
        recv_tokens_per_expert,
    ) = torch.ops.umdk_cam_op_lib.moe_dispatch_prefill(**dispatch_args)

    dist.barrier()

    if local_rank == 0:
        print(f"[dispatch] Completed, received {recv_x.size(0)} tokens")
    
    combine_args = {
        'x': recv_x,
        'topk_idx': topk_idx,
        'topk_weights': topk_weights,
        'src_idx': expand_idx_out,
        'send_head': recv_count,
        'group_ep': ep_hcomm_info,
        'rank': rank,
        'num_ranks': num_ranks,
    }

    combined_x = torch.ops.umdk_cam_op_lib.moe_combine_prefill(**combine_args)
    
    if local_rank == 0:
        print(f"[combine] Completed, output shape: {combined_x.shape}")
        print(f"[combine] output dtype: {combined_x.dtype}")
        

def test_loop(local_rank, num_local_ranks, args):
    num_nodes = int(os.getenv('WORLD_SIZE', 1))
    rank, num_ranks, group = init_dist(local_rank, num_local_ranks)
    
    test_main(args, local_rank, num_local_ranks, num_ranks, num_nodes, rank, group)
    
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