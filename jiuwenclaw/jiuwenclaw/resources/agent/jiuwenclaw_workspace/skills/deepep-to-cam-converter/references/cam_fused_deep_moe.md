# CAM fused moe運算元
## 使用場景
提供在A3環境上執行的用於MoE Decode階段的通算大融合運算元，透過融合[Dispatch + FFN(GMM1 + Swiglu + GMM2) + Combine]實現高效的模型推理和專家選擇，在如下約束下可使用
1. 用於Moe Decode階段，需要嚴格滿足[Dispatch + FFN(GMM1 + Swiglu + GMM2) + Combine]的正規化，其中FFN即專家部分，GMM1是使用分組矩陣乘法進行升維，GMM2是使用分組矩陣乘法降維，啟用函式必須為Swiglu。
2. 當前介面只支援A3環境呼叫。
3. 當前介面不支援併發呼叫。極端情況下在單次forward中連續呼叫相同運算元會產生未定義行為，這種場景需要在運算元執行間新增torch.npu.synchronize()避免潛在的非同步時序問題。
4. 當前介面圖模式只支援AclGraph模式。
5. 不支援外接共享專家（即有的卡只放置共享專家）。
6. 引數範圍要求：
 - 單個裝置上在一次前向傳播中處理的樣本數量為BS，取值範圍[0, 256]
 - 單個token的長度為token_length，取值範圍[1024， 7168]且(token_length % 256) == 0
 - GMM1的權重矩陣為gmm1_weight，隱藏層的維度為gmm1_hiden_size，取值範圍[1024， 6144]且(gmm1_hiden_size % 256) == 0
 - 共享專家MM1的權重矩陣為share_gmm1_weight，隱藏層維度為share_mm1_hidden_size，取值範圍[1024， 6144]且(gmm1_hiden_size % 256) == 0
 - Moe會選擇機率最高的K個專家，將token透過dispatch運算元分發給對應的專家並透過combine運算元收回，當前這套運算元需要保證這個top_k取值範圍為[0, 12]且應保證小於等於專家數
 - 所有卡的最大token總數為global_bs ≥ 0 且保證（global_bs % ep_rank_size） == 0
 - 需要滿足: 路由專家卡需滿足local_expert_num ≤ (aivnum / 2)，其中aivnum為硬體aiv核心數
 - 需要滿足: gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale四個入參的模式必須統一，不能一部分耦合模式一部分分離模式
 - 需要滿足: HCCL_BUFFERSIZE環境變數配置應不小於[(ep_rank_size * max_batch_size * moe_expert_num_per_rank * total_length * sizeof(x) * 2) / 1024 / 1024]向上取整
 - 需要滿足: 若要進行內建共享專家計算，則共享專家所需的share_gmm1_weight、share_gmm1_weight_scale、share_gmm2_weight、share_gmm2_weight_scale需同時存在
- 需要滿足: 若要進行smooth quant，需傳入expert_smooth_scales，若同時進行內建共享專家計算則share_smooth_scales也必須存在

## 介面說明文件
當前提供運算元已提供torch擴充套件包，需要import umdk_cam_op_lib，呼叫時使用torch.ops.umdk_cam_op_lib.xxx進行呼叫
### 2.1 fused_deep_moe ▶
#### 2.1.1 介面原型 
```python
fused_deep_moe(
    Tensor x, 
    Tensor expert_ids, 
    Tensor[] gmm1_weight, 
    Tensor[] gmm1_weight_scale, 
    Tensor[] gmm2_weight, 
    Tensor[] gmm2_weight_scale, 
    Tensor expert_scales, 
    Tensor? share_gmm1_weight, 
    Tensor? share_gmm1_weight_scale, 
    Tensor? share_gmm2_weight, 
    Tensor? share_gmm2_weight_scale, 
    Tensor? expert_smooth_scales,
    Tensor? share_smooth_scales,
    Tensor? x_active_mask, 
    str group_ep, 
    int ep_rank_size, 
    int ep_rank_id, 
    int moe_expert_num, 
    int quant_mode, 
    int global_bs) 
-> output: Tensor[]
```
#### 2.1.2 介面描述 
用於MoE Decode階段的通算大融合運算元，透過融合[Dispatch + FFN(GMM1 + Swiglu + GMM2) + Combine]實現高效的模型推理和專家選擇，（可選）同時支援內建共享專家計算，適用於分散式推理場景。
#### 2.1.3 入參 
| **📌引數** | **🔧型別** | **✅是否必選** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|--------------|----------|
|x|Tensor|必選|形狀:(batch_size, token_length), 支援bf16, float16型別|本卡dispatch階段待處理的token|
|expert_ids|Tensor|必選|形狀:(batch_size, topk)， 資料型別為int32, 取值範圍[-1, num_experts)，-1用於佔位使用，一個token不允許重複發給同一個專家|每個token的目標專家ID資訊|
|gmm1_weight|Tensor[]|必選|耦合模式下，只有一個Tensor, 形狀:(localExpertNum, token_length, gmm1_hidden_size); 分離模式下，包含localExpertNum個Tensor, 每個Tensor形狀：（token_length, gmm1_hidden_size），資料型別為int8|GMM1的權重矩陣列表，支援耦合模式和分離模式|
|gmm1_weight_scale|Tensor[]|必選|耦合模式下，只有一個Tensor, 形狀:(localExpertNum, gmm1_hidden_size); 分離模式下，包含localExpertNum個Tensor, 每個Tensor形狀：（gmm1_hidden_size），資料型別為float32或與x資料型別一致|GMM1的權重矩陣量化時使用的縮放係數列表，支援耦合模式和分離模式|
|gmm2_weight|Tensor[]|必選|耦合模式下，只有一個Tensor, 形狀:(localExpertNum, gmm1_hidden_size/2, token_length); 分離模式下，包含localExpertNum個Tensor, 每個Tensor形狀：（gmm1_hidden_size/2, token_length），資料型別為int8|GMM2的權重矩陣列表，支援耦合模式和分離模式|
|gmm2_weight_scale|Tensor[]|必選|耦合模式下，只有一個Tensor, 形狀:(localExpertNum, token_length); 分離模式下，包含localExpertNum個Tensor, 每個Tensor形狀：（token_length），資料型別為float32或與x資料型別一致|GMM2的權重矩陣量化時使用的縮放係數列表，支援耦合模式和分離模式|
|expert_scales|Tensor|必選|形狀：(batch_size, topk), 資料型別為float32|每個專家的權重，combine階段使用|
|share_gmm1_weight|Tensor|可選|形狀：（token_length, share_mm1_hidden_size），資料型別為int8|共享專家MM1的權重矩陣|
|share_gmm1_weight_scale|Tensor|可選|形狀：（share_mm1_hidden_size），資料型別為與gmm1_weight_scale一致|共享專家MM1的權重矩陣量化時使用的縮放係數|
|share_gmm2_weight|Tensor|可選|形狀：（share_mm1_hidden_size/2, token_length），資料型別為int8|共享專家MM2的權重矩陣|
|share_gmm2_weight_scale|Tensor|可選|形狀：（token_length），資料型別為與gmm2_weight_scale一致|共享專家MM2的權重矩陣量化時使用的縮放係數|
|expert_smooth_scales|Tensor|可選|形狀：(moe_expert_num，token_length)，資料型別為float32|各個路由專家的smooth quant平滑因子|
|share_smooth_scales|Tensor|可選|形狀：(token_length)，資料型別為float32|共享專家的smooth quant平滑因子|
|x_active_mask|Tensor|可選|形狀： (batch_size)，資料型別bool，取值範圍[true, false]，true值一定要在false之前|dispatch分發token時的mask，true代表正常分發該token，false代表不分發|
|group_ep|str|必選|字串長度範圍：(0, 128), 且需要保證是有效的通訊域名稱|HCCL通訊域名稱|
|ep_rank_size|int|必選|需要滿足：(ep_rank_size * MoeExpertNumPerRank) ≤ 512且ep_rank_size > 0|EP通訊域大小|
|ep_rank_id|int|必選|[0, ep_rank_size)|本卡在通訊域中的rankID|
|moe_expert_num|int|必選|需要滿足：moe_expert_num % ep_rank_size == 0|MOE專家數量|
|quant_mode|int|必選|預留入參，當前只支援傳0|量化模式|
|global_bs|int|必選|若所有卡的token數量一致，可以傳入0或者batch_size * ep_rank_size; 若所有卡的token數量不一致，需要傳入max_batch_size * ep_rank_size|所有卡的最大token總數|
#### 2.1.4 返回值 
函式返回值是一個Tensor列表，存放combine_x和expert_token_nums資訊。
| **📌引數** | **🔧型別** | **📋取值說明** | **📝描述** |
|----------|----------|--------------|----------|
|combine_x|Tensor|形狀：(batch_size, token_length)。資料型別與x一致|當前rank上token經各個專家處理後匯聚的結果|
|share_output|Tensor|形狀：(batch_size, token_length)。資料型別與x一致|內建共享專家處理後的結果，即使不進行共享專家計算，也會返回該值佔位|
|expert_token_nums|Tensor|形狀：(local_expert_num)。資料型別為int64|本卡各個專家收到的token數量|
#### 2.1.5 約束和注意事項 ⚠️
1. 入參形狀需嚴格滿足上述入參描述中的形狀定義。
2. 當前介面只支援A3環境呼叫。
3. 當前介面不支援併發呼叫。極端情況下在單次forward中連續呼叫相同運算元會產生未定義行為，這種場景需要在運算元執行間新增torch.npu.synchronize()避免潛在的非同步時序問題。
4. 當前介面圖模式只支援AclGraph模式。
5. 不支援外接共享專家（即有的卡只放置共享專家）。
6. Batch_size小於16時非目標場景，其效能相對於小運算元拼接可能劣化，建議效能對比後決策使用。
7. 除滿足上述形狀約束外，其他引數取值要求：
 - 需要滿足：BS取值範圍[0, 256]
 - 需要滿足: token_length取值範圍[1024， 7168]且(token_length % 256) == 0
 - 需要滿足: gmm1_hiden_size取值範圍[1024， 6144]且(gmm1_hiden_size % 256) == 0
 - 需要滿足: share_mm1_hidden_size取值範圍[1024， 6144]且(gmm1_hiden_size % 256) == 0
 - 需要滿足: topk取值範圍[0, 12]且應保證小於等於專家數
 - 需要滿足：global_bs ≥ 0 且保證（global_bs % ep_rank_size） == 0
 - 需要滿足: 路由專家卡需滿足local_expert_num ≤ (aivnum / 2)，其中aivnum為硬體aiv核心數
 - 需要滿足: gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale四個入參的模式必須統一，不能一部分耦合模式一部分分離模式
 - 需要滿足: HCCL_BUFFERSIZE環境變數配置應不小於[(ep_rank_size * max_batch_size * moe_expert_num_per_rank * total_length * sizeof(x) * 2) / 1024 / 1024]向上取整
 - 需要滿足: 若要進行內建共享專家計算，則共享專家所需的share_gmm1_weight、share_gmm1_weight_scale、share_gmm2_weight、share_gmm2_weight_scale需同時存在
- 需要滿足: 若要進行smooth quant，需傳入expert_smooth_scales，若同時進行內建共享專家計算則share_smooth_scales也必須存在

### 示例1：[Dispatch + FFN(GMM1 + Swiglu + GMM2) + Combine]替換為fused deep moe運算元
替換前：
```python
import torch
import numpy as np
import torch.distributed as dist
from collections import defaultdict
import gc
import os
import sys
import math
import socket
from flashinfer.cute_dsl import grouped_gemm_nt_masked
import torch.nn.functional as F
import deep_ep

def convert_tensor_into_parameter(tensor):
    if tensor is None:
        return None
    return torch.nn.Parameter(tensor, requires_grad=False)

def dequant_swiglu_quant_gpu(y1_int32, weight_scale, activation_scale, group_list):
    dequantized = y1_int32.to(torch.float32) * (activation_scale * weight_scale)
    dequantized = dequantized.to(torch.bfloat16)

    intermediate_dim = dequantized.shape[-1] // 2
    up_proj = dequantized[:, :intermediate_dim]
    gate_proj = dequantized[:, intermediate_dim:]

    swiglu_out = up_proj * F.silu(gate_proj.to(torch.float32)).to(torch.bfloat16)

    abs_max = torch.abs(swiglu_out).max(dim=-1, keepdim=True)[0]
    y1_scale = abs_max / 127.0

    safe_scale = torch.clamp(y1_scale, min=1e-8)
    y1_float = swiglu_out.to(torch.float32) / safe_scale
    y1 = torch.clamp(torch.round(y1_float), -128, 127).to(torch.int8)
    
    return y1, y1_scale.squeeze(-1)

class CustomOps(torch.nn.Module):

    def __init__(self,
                 ep_hcomm_info,
                 meta_info,
                 weight_datas,
                 share_weight_datas):
        super().__init__()
        self.ep_hcomm_info = ep_hcomm_info
        batch_size, ep_world_size, moe_expert_num, global_rank_id, dynamic_eplb = meta_info
        self.ep_world_size = ep_world_size
        self.moe_expert_num = moe_expert_num
        self.global_rank_id = global_rank_id
        self.dynamic_eplb = dynamic_eplb
        self.global_batch_size = batch_size * ep_world_size
        self.with_share = None
        self.with_smooth = None
        self._checkout_datas(weight_datas, share_weight_datas)
        self._process_share_weights_after_loading(share_weight_datas)
        self._process_weights_after_loading(weight_datas)

    def _checkout_datas(self, weight_datas, share_weight_datas):
        gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales = weight_datas
        share_mm1_weight, share_mm1_weight_scale, share_mm2_weight, share_mm2_weight_scale, share_smooth_scales = share_weight_datas
        if share_mm1_weight is not None:
            assert share_mm1_weight_scale is not None, "share expert need share_mm1_weight_scale"
            assert share_mm2_weight is not None, "share expert need share_mm2_weight"
            assert share_mm2_weight_scale is not None, "share expert need share_mm2_weight_scale"
            if smooth_scales is not None:
                assert share_smooth_scales is not None, "share expert need share_smooth_scales"
                self.with_smooth = True
            else:
                self.with_smooth = False
            self.with_share = True
        else:
            self.with_share = False

    def _process_share_weights_after_loading(self, share_weight_datas):
        share_gmm1_weight, share_gmm1_weight_scale, share_gmm2_weight, share_gmm2_weight_scale, share_smooth_scales = share_weight_datas
        self.share_gmm1_weight = convert_tensor_into_parameter(share_gmm1_weight)
        self.share_gmm1_weight_scale = convert_tensor_into_parameter(share_gmm1_weight_scale)
        self.share_gmm2_weight = convert_tensor_into_parameter(share_gmm2_weight)
        self.share_gmm2_weight_scale = convert_tensor_into_parameter(share_gmm2_weight_scale)
        self.share_smooth_scales = convert_tensor_into_parameter(share_smooth_scales)

    def _process_weights_after_loading(self, weight_datas):
        gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales = weight_datas
        self.gmm1_weight = convert_tensor_into_parameter(gmm1_weight)
        self.gmm1_weight_scale = convert_tensor_into_parameter(gmm1_weight_scale)
        self.gmm2_weight = convert_tensor_into_parameter(gmm2_weight)
        self.gmm2_weight_scale = convert_tensor_into_parameter(gmm2_weight_scale)
        self.smooth_scales = convert_tensor_into_parameter(smooth_scales)

    def _apply_ops(self, x, expert_ids, expert_scales, x_active_mask, buffer):
        raise NotImplementedError("To be implemented in subclass")

    def forward(self, x, expert_ids, expert_scales, x_active_mask, buffer):
        return self._apply_ops(x, expert_ids, expert_scales, x_active_mask, buffer)

class Ops(CustomOps):
    def __init__(self,
                 ep_hcomm_info,
                 meta_info,
                 weight_datas,
                 share_weight_datas):
        super().__init__(ep_hcomm_info, meta_info, weight_datas, share_weight_datas)
        self.shared_expert_rank_num = 0
        self.tp_hcomm_info = ""

    def _dynamic_quant(self, x):
        x_fp16 = x / self.share_smooth_scales if self.share_smooth_scales is not None else x
        scale = torch.abs(x_fp16).max(dim=-1, keepdim=True)[0] / 127.0
        scale = torch.clamp(scale, min=1e-8)
        x_int8 = torch.clamp(torch.round(x_fp16 / scale), -128, 127).to(torch.int8)
        return x_int8, scale.squeeze(-1)

    def _quant_matmul(self, x_int8, weight, weight_scale, pertoken_scale=None, output_dtype=None):
        if pertoken_scale is not None:
            scale = (pertoken_scale * weight_scale).to(output_dtype)
            result = (x_int8.to(output_dtype) @ weight.to(output_dtype)) * scale.unsqueeze(-1)
        else:
            result = (x_int8.to(torch.int32) @ weight.to(torch.int32)).to(output_dtype)
        return result

    def _swiglu_quant(self, x, weight_scale, activation_scale):
        # Dequant
        scale_factor = (activation_scale * weight_scale).to(torch.float16)
        x_fp16 = x.to(torch.float16) * scale_factor.unsqueeze(-1)
        
        # SwiGLU (activate_left=True, quant_mode=1)
        split = x_fp16.shape[-1] // 2
        swiglu_fp16 = x_fp16[..., :split] * torch.sigmoid(x_fp16[..., :split])
        
        # Quant
        scale = torch.abs(swiglu_fp16).max(dim=-1, keepdim=True)[0] / 127.0
        scale = torch.clamp(scale, min=1e-8)
        x_int8 = torch.clamp(torch.round(swiglu_fp16 / scale), -128, 127).to(torch.int8)
        
        return x_int8, scale.squeeze(-1)

    def share_compute(self, x):
        x1_int8, x1_scale = self._dynamic_quant(x)
        gmm1_result = self._quant_matmul(x1_int8, self.share_gmm1_weight, self.share_gmm1_weight_scale, pertoken_scale=None, output_dtype=torch.int32)
        x2_int8, x2_scale = self._swiglu_quant(gmm1_result, self.share_gmm1_weight_scale, x1_scale)
        gmm2_result = self._quant_matmul(x2_int8, self.share_gmm2_weight, self.share_gmm2_weight_scale, pertoken_scale=x2_scale, output_dtype=x.dtype)
        return gmm2_result

    def _apply_ops(self, x, expert_ids, expert_scales, x_active_mask, buffer):
        if self.with_share:
            share_output = self.share_compute(x)
        else:
            share_output = None

        num_tokens_per_rank, num_tokens_per_rdma_rank, num_tokens_per_expert, is_token_in_rank, _ = \
            buffer.get_dispatch_layout(expert_ids, self.moe_expert_num)

        num_sms = 24
        rdma_buffer_size, nvl_buffer_size = 128, 512
        config = deep_ep.Config(num_sms, 8, nvl_buffer_size, 16, rdma_buffer_size)

        dispatch_args = {
            'x': x,
            'num_tokens_per_rank': num_tokens_per_rank,
            'num_tokens_per_rdma_rank': num_tokens_per_rdma_rank,
            'is_token_in_rank': is_token_in_rank,
            'num_tokens_per_expert': num_tokens_per_expert,
            'config': config,
            'async_finish': False,
            'topk_idx': expert_ids,
            'topk_weights': expert_scales,
            'use_fp8' : True,
        }

        recv, _, handle, event = buffer.dispatch(**dispatch_args)
        recv_x = recv[0]
        recv_x_scales = recv[1]
        event.current_stream_wait()

        output_dtype = x.dtype

        y1_int32 = grouped_gemm_nt_masked(
            recv_x,
            self.gmm1_weight,
            num_tokens_per_expert,
        )
        y1, y1_scale = dequant_swiglu_quant_gpu(
            y1_int32,
            self.gmm1_weight_scale,
            recv_x_scales,
            num_tokens_per_expert,
        )
        y2 = grouped_gemm_nt_masked(
            y1,
            self.gmm2_weight,
            num_tokens_per_expert,
            scale=self.gmm2_weight_scale,
            per_token_scale=y1_scale,
            output_dtype=torch.bfloat16,
        )
        combine_args = {
            'x': recv_x,
            'bias': (torch.ones_like(recv_x), torch.zeros_like(recv_x)),
            'handle': handle,
            'config': config,
            'async_finish': False,
            'topk_weights': expert_scales
        }

        combine_output, event = buffer.combine(**combine_args)
        event.current_stream_wait()
        return (combine_output, share_output, num_tokens_per_expert)

def generate_datas(batch_size,
                   token_hidden_size,
                   moe_intermediate_size,
                   ep_world_size,
                   moe_expert_num,
                   global_rank_id,
                   top_k=8,
                   enable_dynamic_bs=False,
                   with_mc2_mask=False,
                   with_share=False,
                   with_smooth=False,
                   share_expert_intermediate_size=None):
    moe_expert_num_per_rank = moe_expert_num // ep_world_size
    actual_bs = int(
        np.random.randint(2 if with_mc2_mask else 1, batch_size)
        if enable_dynamic_bs else batch_size)
    local_expert_num = moe_expert_num_per_rank
    gmm1_input_dim = token_hidden_size
    gmm1_output_dim = moe_intermediate_size * 2
    gmm2_input_dim = moe_intermediate_size
    gmm2_output_dim = token_hidden_size
    x = np.random.rand(actual_bs, token_hidden_size).astype(np.float32) * 10 - 5
    expert_ids = np.arange(
        global_rank_id * batch_size * top_k,
        global_rank_id * batch_size * top_k + actual_bs * top_k,
        dtype=np.int32).reshape(actual_bs, top_k)
    expert_ids = expert_ids % moe_expert_num
    gmm1_weight = np.random.randint(
        -16, 16,
        [local_expert_num, gmm1_input_dim, gmm1_output_dim]).astype(np.int8)
    gmm2_weight = np.random.randint(
        -16, 16,
        [local_expert_num, gmm2_input_dim, gmm2_output_dim]).astype(np.int8)
    gmm1_weight_scale = (np.random.rand(local_expert_num, gmm1_output_dim
                                        ).astype(np.float32) * 0.003 + 0.0015)
    gmm2_weight_scale = (np.random.rand(local_expert_num, gmm2_output_dim
                                        ).astype(np.float32) * 0.003 + 0.0015)
    expert_scales = np.random.rand(actual_bs, top_k).astype(np.float32)
    # Generate shared expert weights
    share_mm1_weight = None
    share_mm1_weight_scale = None
    share_mm2_weight = None
    share_mm2_weight_scale = None
    if with_share:
        # Use share_expert_intermediate_size for shared expert gmm1HLen
        share_gmm2_input_dim = share_expert_intermediate_size if share_expert_intermediate_size is not None else moe_intermediate_size
        share_gmm1_output_dim = share_gmm2_input_dim * 2
        share_mm1_weight = np.ones([gmm1_input_dim, share_gmm1_output_dim]).astype(np.int8) * 4
        share_mm2_weight = np.ones([share_gmm2_input_dim, gmm2_output_dim]).astype(np.int8) * 4
        share_mm1_weight_scale = np.ones([share_gmm1_output_dim]) * 0.0015
        share_mm2_weight_scale = np.ones([gmm2_output_dim]) * 0.0015
        share_mm1_weight[:, ::2] = share_mm1_weight[:, ::2] * -1
        share_mm2_weight[:, ::2] = share_mm2_weight[:, ::2] * -1
    smooth_scales = None
    share_smooth_scales = None
    if with_smooth:
        smooth_scales = torch.rand([moe_expert_num, token_hidden_size])
        share_smooth_scales = torch.rand([token_hidden_size]).to(x.dtype)
    x_active_mask = None
    valid_token_num = actual_bs
    if with_mc2_mask:
        valid_token_num = int(np.random.randint(1, actual_bs))
        x_active_mask = np.concatenate(
            [np.ones(valid_token_num),
             np.zeros(actual_bs - valid_token_num)]).astype(bool)
    return (x, expert_ids, expert_scales, x_active_mask), \
            (gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales), \
            (share_mm1_weight, share_mm1_weight_scale, share_mm2_weight, share_mm2_weight_scale, share_smooth_scales), \
            actual_bs, valid_token_num

CASE_4RANK = {
    "totalExpertNum": 16,
    "topk": 8,
    "batchSize": 16,
    "hiddenSize": 7168,
    "intermediateHiddenSize": 2048,
    "dynamicEPLB": False,
    "with_mc2_mask": False,
}

CASE_8RANK = {
    "totalExpertNum": 16,
    "topk": 8,
    "batchSize": 32,
    "hiddenSize": 7168,
    "intermediateHiddenSize": 2048,
    "dynamicEPLB": True,
    "with_mc2_mask": False,
}

def test_base_test():
    rank = int(os.environ.get("RANK", 0))
    worldSize = int(os.environ.get("WORLD_SIZE", 1))
    ip = os.getenv('MASTER_ADDR', '127.0.0.1')
    port = int(os.getenv('MASTER_PORT', '8361'))

    case = CASE_4RANK
    totalExpertNum = case["totalExpertNum"]
    topk = case["topk"]
    hiddenSize = case["hiddenSize"]
    intermediateHiddenSize = case["intermediateHiddenSize"]
    batchSize = case["batchSize"]
    dynamicEPLB = case["dynamicEPLB"]
    with_mc2_mask = case["with_mc2_mask"]
    test_bfloat16 = True

    torch.cuda.set_device(rank)
    device = torch.device(f"cuda:{rank}")
    dist.init_process_group(
        backend='nccl',
        device_id = device,
        rank=rank,
        world_size=worldSize
    )
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(device)

    ep_ranks_list = list(np.arange(0, worldSize))
    ep_group = dist.new_group(backend="nccl", ranks=ep_ranks_list)

    ep_hcomm_info = ep_group._get_backend(
        torch.device("cuda")).get_hccl_comm_name(rank)

    torch.cuda.synchronize()
    
    # 構造輸入資料
    dynamicBS = False
    with_share = False
    with_smooth = False
    share_expert_intermediate_size = 0
    parameter = (batchSize, hiddenSize, intermediateHiddenSize,
                 worldSize, totalExpertNum, rank, topk, dynamicBS, with_mc2_mask,
                 with_share, with_smooth, share_expert_intermediate_size)
    input_datas, weight_datas, share_weight_datas, actual_bs, valid_token_num = generate_datas(*parameter)

    x_dtype = torch.bfloat16 if test_bfloat16 else torch.float16
    scale_dtype = torch.bfloat16 if test_bfloat16 else torch.float32
    x_np, expert_ids_np, expert_scales_np, x_active_mask_np = input_datas
    buffer = deep_ep.Buffer(
    num_ranks=worldSize,
    hidden_size=hiddenSize,
    use_fp8=True,
    round_scale=True,
    use_ue8m0=True,
    )
    input_datas = [
        torch.from_numpy(x_np).to(dtype=x_dtype).cuda(),
        torch.from_numpy(expert_ids_np).cuda(),
        torch.from_numpy(expert_scales_np).cuda(),
        torch.from_numpy(x_active_mask_np).cuda() if x_active_mask_np is not None else None,
        buffer,
    ]
    meta_info = (batchSize, worldSize, totalExpertNum, rank, dynamicEPLB)
    gmm1_w, gmm1_ws, gmm2_w, gmm2_ws, smooth_scales = weight_datas
    weight_datas = [
        torch.from_numpy(gmm1_w).cuda(),
        torch.from_numpy(gmm1_ws).float().cuda(),
        torch.from_numpy(gmm2_w).cuda(),
        torch.from_numpy(gmm2_ws).to(dtype=scale_dtype).cuda(),
        None if smooth_scales is None else torch.from_numpy(smooth_scales).float().cuda()
    ]
    share_mm1_w, share_mm1_ws, share_mm2_w, share_mm2_ws, share_smooth_scales = share_weight_datas
    share_weight_datas = [
        None if share_mm1_w is None else torch.from_numpy(share_mm1_w).cuda(),
        None if share_mm1_ws is None else torch.from_numpy(share_mm1_ws).float().cuda(),
        None if share_mm2_w is None else torch.from_numpy(share_mm2_w).cuda(),
        None if share_mm2_ws is None else torch.from_numpy(share_mm2_ws).to(dtype=scale_dtype).cuda(),
        None if share_smooth_scales is None else torch.from_numpy(share_smooth_scales).to(x_dtype).cuda()
    ]
    ops = Ops(ep_hcomm_info, meta_info, weight_datas, share_weight_datas).cuda()
    op_token_output, op_share_output, op_count_output = ops(*input_datas)
    torch.cuda.synchronize()

    if with_share:
        share_token_np = op_share_output.cpu().float().numpy()
if __name__ == "__main__":
    test_base_test()
```

替換後：
```python
import torch
import torch_npu
import numpy as np
import torch.distributed as dist
from collections import defaultdict
import gc
import os
import sys
import math
import socket
import umdk_cam_op_lib

torch_npu.npu.config.allow_internal_format = True

def convert_tensor_into_parameter(tensor, trans_nz=False):
    if tensor is None:
        return None
    if trans_nz:
        tensor = torch_npu.npu_format_cast(tensor, torch_npu.Format.FRACTAL_NZ)
    return torch.nn.Parameter(tensor, requires_grad=False)

class CustomOps(torch.nn.Module):

    def __init__(self,
                 ep_hcomm_info,
                 meta_info,
                 weight_datas,
                 share_weight_datas):
        super().__init__()
        self.ep_hcomm_info = ep_hcomm_info
        batch_size, ep_world_size, moe_expert_num, global_rank_id, dynamic_eplb = meta_info
        self.ep_world_size = ep_world_size
        self.moe_expert_num = moe_expert_num
        self.global_rank_id = global_rank_id
        self.dynamic_eplb = dynamic_eplb
        self.global_batch_size = batch_size * ep_world_size
        self.with_share = None
        self.with_smooth = None
        self._checkout_datas(weight_datas, share_weight_datas)
        self._process_share_weights_after_loading(share_weight_datas)
        self._process_weights_after_loading(weight_datas)

    def _checkout_datas(self, weight_datas, share_weight_datas):
        gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales = weight_datas
        share_mm1_weight, share_mm1_weight_scale, share_mm2_weight, share_mm2_weight_scale, share_smooth_scales = share_weight_datas
        if share_mm1_weight is not None:
            assert share_mm1_weight_scale is not None, "share expert need share_mm1_weight_scale"
            assert share_mm2_weight is not None, "share expert need share_mm2_weight"
            assert share_mm2_weight_scale is not None, "share expert need share_mm2_weight_scale"
            if smooth_scales is not None:
                assert share_smooth_scales is not None, "share expert need share_smooth_scales"
                self.with_smooth = True
            else:
                self.with_smooth = False
            self.with_share = True
        else:
            self.with_share = False

    def _process_share_weights_after_loading(self, share_weight_datas):
        share_gmm1_weight, share_gmm1_weight_scale, share_gmm2_weight, share_gmm2_weight_scale, share_smooth_scales = share_weight_datas
        self.share_gmm1_weight = convert_tensor_into_parameter(share_gmm1_weight, trans_nz=True)
        self.share_gmm1_weight_scale = convert_tensor_into_parameter(share_gmm1_weight_scale)
        self.share_gmm2_weight = convert_tensor_into_parameter(share_gmm2_weight, trans_nz=True)
        self.share_gmm2_weight_scale = convert_tensor_into_parameter(share_gmm2_weight_scale)
        self.share_smooth_scales = convert_tensor_into_parameter(share_smooth_scales)

    def _process_weights_after_loading(self, weight_datas):
        gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales = weight_datas
        self.gmm1_weight = convert_tensor_into_parameter(gmm1_weight, trans_nz=True)
        self.gmm1_weight_scale = convert_tensor_into_parameter(gmm1_weight_scale)
        self.gmm2_weight = convert_tensor_into_parameter(gmm2_weight, trans_nz=True)
        self.gmm2_weight_scale = convert_tensor_into_parameter(gmm2_weight_scale)
        self.smooth_scales = convert_tensor_into_parameter(smooth_scales)

    def _apply_ops(self, x, expert_ids, expert_scales, x_active_mask):
        raise NotImplementedError("To be implemented in subclass")

    def forward(self, x, expert_ids, expert_scales, x_active_mask):
        return self._apply_ops(x, expert_ids, expert_scales, x_active_mask)


class Ops(CustomOps):

    def __init__(self,
                 ep_hcomm_info,
                 meta_info,
                 weight_datas,
                 share_weight_datas):
        super().__init__(ep_hcomm_info, meta_info, weight_datas, share_weight_datas)

    def _apply_ops(self, x, expert_ids, expert_scales, x_active_mask):
        output, share_output, expert_token_nums = torch.ops.umdk_cam_op_lib.fused_deep_moe(
            x=x,
            expert_ids=expert_ids,
            gmm1_weight=self.gmm1_weight,
            gmm1_weight_scale=self.gmm1_weight_scale,
            gmm2_weight=self.gmm2_weight,
            gmm2_weight_scale=self.gmm2_weight_scale,
            expert_scales=expert_scales,
            share_gmm1_weight=self.share_gmm1_weight,
            share_gmm1_weight_scale=self.share_gmm1_weight_scale,
            share_gmm2_weight=self.share_gmm2_weight,
            share_gmm2_weight_scale=self.share_gmm2_weight_scale,
            expert_smooth_scales=self.smooth_scales,
            share_smooth_scales=self.share_smooth_scales_fp32,
            x_active_mask=x_active_mask,
            group_ep=self.ep_hcomm_info,
            ep_rank_size=self.ep_world_size,
            ep_rank_id=self.global_rank_id,
            moe_expert_num=self.moe_expert_num,
            quant_mode=0,
            global_bs=self.global_batch_size)
        return (output, share_output, expert_token_nums)

    def _process_share_weights_after_loading(self, share_weight_datas):
        super()._process_share_weights_after_loading(share_weight_datas)
        _, _, _, _, share_smooth_scales = share_weight_datas
        if self.with_share and self.with_smooth:
            self.share_smooth_scales_fp32 = convert_tensor_into_parameter(share_smooth_scales.float())
        else:
            self.share_smooth_scales_fp32 = None

    def _process_weights_after_loading(self, weight_datas):
        gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales = weight_datas
        gmm1_weight = convert_tensor_into_parameter(gmm1_weight, trans_nz=True)
        gmm1_weight_scale = convert_tensor_into_parameter(gmm1_weight_scale)
        gmm2_weight = convert_tensor_into_parameter(gmm2_weight, trans_nz=True)
        gmm2_weight_scale = convert_tensor_into_parameter(gmm2_weight_scale)
        if self.dynamic_eplb:
            self.gmm1_weight = [
                weight.clone() for weight in gmm1_weight.unbind(dim=0)
            ]
            self.gmm1_weight_scale = [
                weight.clone() for weight in gmm1_weight_scale.unbind(dim=0)
            ]
            self.gmm2_weight = [
                weight.clone() for weight in gmm2_weight.unbind(dim=0)
            ]
            self.gmm2_weight_scale = [
                weight.clone() for weight in gmm2_weight_scale.unbind(dim=0)
            ]
        else:
            self.gmm1_weight = [gmm1_weight.clone()]
            self.gmm1_weight_scale = [gmm1_weight_scale.clone()]
            self.gmm2_weight = [gmm2_weight.clone()]
            self.gmm2_weight_scale = [gmm2_weight_scale.clone()]
        self.smooth_scales = convert_tensor_into_parameter(smooth_scales)

def generate_datas(batch_size,
                   token_hidden_size,
                   moe_intermediate_size,
                   ep_world_size,
                   moe_expert_num,
                   global_rank_id,
                   top_k=8,
                   enable_dynamic_bs=False,
                   with_mc2_mask=False,
                   with_share=False,
                   with_smooth=False,
                   share_expert_intermediate_size=None):
    moe_expert_num_per_rank = moe_expert_num // ep_world_size
    actual_bs = int(
        np.random.randint(2 if with_mc2_mask else 1, batch_size)
        if enable_dynamic_bs else batch_size)
    local_expert_num = moe_expert_num_per_rank
    gmm1_input_dim = token_hidden_size
    gmm1_output_dim = moe_intermediate_size * 2
    gmm2_input_dim = moe_intermediate_size
    gmm2_output_dim = token_hidden_size
    x = np.random.rand(actual_bs, token_hidden_size).astype(np.float32) * 10 - 5
    expert_ids = np.arange(
        global_rank_id * batch_size * top_k,
        global_rank_id * batch_size * top_k + actual_bs * top_k,
        dtype=np.int32).reshape(actual_bs, top_k)
    expert_ids = expert_ids % moe_expert_num
    gmm1_weight = np.random.randint(
        -16, 16,
        [local_expert_num, gmm1_input_dim, gmm1_output_dim]).astype(np.int8)
    gmm2_weight = np.random.randint(
        -16, 16,
        [local_expert_num, gmm2_input_dim, gmm2_output_dim]).astype(np.int8)
    gmm1_weight_scale = (np.random.rand(local_expert_num, gmm1_output_dim
                                        ).astype(np.float32) * 0.003 + 0.0015)
    gmm2_weight_scale = (np.random.rand(local_expert_num, gmm2_output_dim
                                        ).astype(np.float32) * 0.003 + 0.0015)
    expert_scales = np.random.rand(actual_bs, top_k).astype(np.float32)
    # Generate shared expert weights
    share_mm1_weight = None
    share_mm1_weight_scale = None
    share_mm2_weight = None
    share_mm2_weight_scale = None
    if with_share:
        # Use share_expert_intermediate_size for shared expert gmm1HLen
        share_gmm2_input_dim = share_expert_intermediate_size if share_expert_intermediate_size is not None else moe_intermediate_size
        share_gmm1_output_dim = share_gmm2_input_dim * 2
        share_mm1_weight = np.ones([gmm1_input_dim, share_gmm1_output_dim]).astype(np.int8) * 4
        share_mm2_weight = np.ones([share_gmm2_input_dim, gmm2_output_dim]).astype(np.int8) * 4
        share_mm1_weight_scale = np.ones([share_gmm1_output_dim]) * 0.0015
        share_mm2_weight_scale = np.ones([gmm2_output_dim]) * 0.0015
        share_mm1_weight[:, ::2] = share_mm1_weight[:, ::2] * -1
        share_mm2_weight[:, ::2] = share_mm2_weight[:, ::2] * -1
    smooth_scales = None
    share_smooth_scales = None
    if with_smooth:
        smooth_scales = torch.rand([moe_expert_num, token_hidden_size])
        share_smooth_scales = torch.rand([token_hidden_size]).to(x.dtype)
    x_active_mask = None
    valid_token_num = actual_bs
    if with_mc2_mask:
        valid_token_num = int(np.random.randint(1, actual_bs))
        x_active_mask = np.concatenate(
            [np.ones(valid_token_num),
             np.zeros(actual_bs - valid_token_num)]).astype(bool)
    return (x, expert_ids, expert_scales, x_active_mask), \
            (gmm1_weight, gmm1_weight_scale, gmm2_weight, gmm2_weight_scale, smooth_scales), \
            (share_mm1_weight, share_mm1_weight_scale, share_mm2_weight, share_mm2_weight_scale, share_smooth_scales), \
            actual_bs, valid_token_num

CASE_4RANK = {
    "totalExpertNum": 16,
    "topk": 8,
    "batchSize": 16,
    "hiddenSize": 7168,
    "intermediateHiddenSize": 2048,
    "dynamicEPLB": False,
    "with_mc2_mask": False,
}

CASE_8RANK = {
    "totalExpertNum": 16,
    "topk": 8,
    "batchSize": 32,
    "hiddenSize": 7168,
    "intermediateHiddenSize": 2048,
    "dynamicEPLB": True,
    "with_mc2_mask": False,
}

def test_base_test():

    rank = int(os.environ.get("RANK", 0))
    worldSize = int(os.environ.get("WORLD_SIZE", 1))
    ip = os.getenv('MASTER_ADDR', '127.0.0.1')
    port = int(os.getenv('MASTER_PORT', '8361'))

    case = CASE_4RANK
    totalExpertNum = case["totalExpertNum"]
    topk = case["topk"]
    hiddenSize = case["hiddenSize"]
    intermediateHiddenSize = case["intermediateHiddenSize"]
    batchSize = case["batchSize"]
    dynamicEPLB = case["dynamicEPLB"]
    with_mc2_mask = case["with_mc2_mask"]
    test_bfloat16 = True

    # 構造通訊域
    torch.npu.set_device(rank)
    device = torch.device(f"npu:{rank}")
    dist.init_process_group(
        backend='hccl',
        device_id = device,
        rank=rank,
        world_size=worldSize
    )
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(device)

    ep_ranks_list = list(np.arange(0, worldSize))
    ep_group = dist.new_group(backend="hccl", ranks=ep_ranks_list)

    ep_hcomm_info = ep_group._get_backend(
        torch.device("npu")).get_hccl_comm_name(rank)
    torch_npu.npu.synchronize()
    
    # 構造輸入資料
    dynamicBS = False
    with_share = False
    with_smooth = False
    share_expert_intermediate_size = 0
    parameter = (batchSize, hiddenSize, intermediateHiddenSize,
                 worldSize, totalExpertNum, rank, topk, dynamicBS, with_mc2_mask,
                 with_share, with_smooth, share_expert_intermediate_size)
    input_datas, weight_datas, share_weight_datas, actual_bs, valid_token_num = generate_datas(*parameter)

    x_dtype = torch.bfloat16 if test_bfloat16 else torch.float16
    scale_dtype = torch.bfloat16 if test_bfloat16 else torch.float32
    x_np, expert_ids_np, expert_scales_np, x_active_mask_np = input_datas
    input_datas = [
        torch.from_numpy(x_np).to(dtype=x_dtype).npu(),
        torch.from_numpy(expert_ids_np).npu(),
        torch.from_numpy(expert_scales_np).npu(),
        torch.from_numpy(x_active_mask_np).npu() if x_active_mask_np is not None else None,
    ]
    meta_info = (batchSize, worldSize, totalExpertNum, rank, dynamicEPLB)
    gmm1_w, gmm1_ws, gmm2_w, gmm2_ws, smooth_scales = weight_datas
    weight_datas = [
        torch.from_numpy(gmm1_w).npu(),
        torch.from_numpy(gmm1_ws).float().npu(),
        torch.from_numpy(gmm2_w).npu(),
        torch.from_numpy(gmm2_ws).to(dtype=scale_dtype).npu(),
        None if smooth_scales is None else torch.from_numpy(smooth_scales).float().npu()
    ]
    share_mm1_w, share_mm1_ws, share_mm2_w, share_mm2_ws, share_smooth_scales = share_weight_datas
    share_weight_datas = [
        None if share_mm1_w is None else torch.from_numpy(share_mm1_w).npu(),
        None if share_mm1_ws is None else torch.from_numpy(share_mm1_ws).float().npu(),
        None if share_mm2_w is None else torch.from_numpy(share_mm2_w).npu(),
        None if share_mm2_ws is None else torch.from_numpy(share_mm2_ws).to(dtype=scale_dtype).npu(),
        None if share_smooth_scales is None else torch.from_numpy(share_smooth_scales).to(x_dtype).npu()
    ]
    ops = Ops(ep_hcomm_info, meta_info, weight_datas, share_weight_datas).npu()
    op_token_output, op_share_output, op_count_output = ops(*input_datas)
    torch_npu.npu.synchronize()
    if with_share:
        share_token_np = op_share_output.cpu().float().numpy()

if __name__ == "__main__":
    test_base_test()
```