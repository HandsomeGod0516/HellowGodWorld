"""
SASRec模型實現（Self-Attentive Sequential Recommendation）
"""

import numpy as np
import torch
import torch.nn as nn


class PointWiseFeedForward(nn.Module):
    """
    逐點前饋神經網路（Point-wise Feed-Forward Network）

    用於Transformer塊中的FFN層，使用兩層1D卷積實現。
    結構：Conv1D -> Dropout -> ReLU -> Conv1D -> Dropout + 殘差連線

    Args:
        hidden_units: 隱藏層維度
        dropout_rate: Dropout機率
    """
    def __init__(self, hidden_units, dropout_rate):
        super(PointWiseFeedForward, self).__init__()

        self.conv1 = torch.nn.Conv1d(hidden_units, hidden_units, kernel_size=1)
        self.dropout1 = torch.nn.Dropout(p=dropout_rate)
        self.relu = torch.nn.ReLU()
        self.conv2 = torch.nn.Conv1d(hidden_units, hidden_units, kernel_size=1)
        self.dropout2 = torch.nn.Dropout(p=dropout_rate)

    def forward(self, inputs):
        # Conv1D需要(N, C, Length)格式，所以需要轉置
        outputs = self.dropout2(self.conv2(self.relu(self.dropout1(self.conv1(inputs.transpose(-1, -2))))))
        outputs = outputs.transpose(-1, -2)  # 轉回(N, Length, C)
        outputs += inputs  # 殘差連線
        return outputs


class SASRec(nn.Module):
    """
    SASRec: Self-Attentive Sequential Recommendation

    Args:
        args: 配置物件，需包含以下欄位：
            - user_num (int): 使用者數量
            - item_num (int): 物品數量
            - hidden_units (int): 隱藏層維度（嵌入維度）
            - maxlen (int): 最大序列長度
            - num_blocks (int): Transformer塊數量
            - num_heads (int): 注意力頭數
            - dropout_rate (float): Dropout機率

    輸入：
        - seqs (Tensor): 使用者行為序列 [batch_size, max_len]
        - target (Tensor): 目標物品ID [batch_size] 或 [batch_size, K]
        - target_posi (Tensor, optional): 目標位置索引 [batch_size, 2]

    輸出：
        - scores (Tensor): 預測分數 [batch_size] 或 [batch_size, K]
    """

    def __init__(self, args):
        super(SASRec, self).__init__()
        self.config = args

        self.user_num = args.user_num
        self.item_num = args.item_num

        # 物品嵌入層（padding_idx=0表示ID=0為填充符）
        self.item_emb = torch.nn.Embedding(self.item_num, args.hidden_units, padding_idx=0)

        # 位置編碼層（可學習的位置嵌入）
        self.pos_emb = torch.nn.Embedding(args.maxlen, args.hidden_units)

        # Embedding Dropout
        self.emb_dropout = torch.nn.Dropout(p=args.dropout_rate)

        # Transformer塊列表
        self.attention_layernorms = torch.nn.ModuleList()  # 注意力層前的LayerNorm
        self.attention_layers = torch.nn.ModuleList()      # 多頭自注意力層
        self.forward_layernorms = torch.nn.ModuleList()    # FFN前的LayerNorm
        self.forward_layers = torch.nn.ModuleList()        # 前饋神經網路

        # 最終的LayerNorm
        self.last_layernorm = torch.nn.LayerNorm(args.hidden_units, eps=1e-8)

        # 構建num_blocks個Transformer塊
        for _ in range(args.num_blocks):
            # 注意力子層
            new_attn_layernorm = torch.nn.LayerNorm(args.hidden_units, eps=1e-8)
            self.attention_layernorms.append(new_attn_layernorm)

            new_attn_layer = torch.nn.MultiheadAttention(
                args.hidden_units,
                args.num_heads,
                args.dropout_rate
            )
            self.attention_layers.append(new_attn_layer)

            # FFN子層
            new_fwd_layernorm = torch.nn.LayerNorm(args.hidden_units, eps=1e-8)
            self.forward_layernorms.append(new_fwd_layernorm)

            new_fwd_layer = PointWiseFeedForward(args.hidden_units, args.dropout_rate)
            self.forward_layers.append(new_fwd_layer)

        # 初始化裝置屬性（避免AttributeError）
        self.dev = self.item_emb.weight.device

    def _device(self):
        """獲取模型所在裝置（更新self.dev）"""
        self.dev = self.item_emb.weight.device

    def log2feats(self, log_seqs):
        """
        將使用者行為序列編碼為特徵向量

        處理流程：
        1. Item Embedding + 縮放
        2. Position Embedding
        3. Dropout
        4. 應用Padding Mask
        5. 透過多個Transformer塊（Self-Attention + FFN）
        6. 最終LayerNorm

        Args:
            log_seqs (Tensor): 使用者行為序列 [batch_size, seq_len]

        Returns:
            log_feats (Tensor): 序列特徵 [batch_size, seq_len, hidden_units]
        """
        # 1. 物品嵌入 + 縮放（類似Transformer論文中的sqrt(d_model)縮放）
        seqs = self.item_emb(log_seqs.to(self.dev))
        seqs *= self.item_emb.embedding_dim ** 0.5

        # 2. 位置編碼（為每個位置新增位置資訊）
        positions = np.tile(np.array(range(log_seqs.shape[1])), [log_seqs.shape[0], 1])
        seqs += self.pos_emb(torch.LongTensor(positions).to(self.dev))
        seqs = self.emb_dropout(seqs)

        # 3. Padding mask（將padding位置（ID=0）的向量置零）
        timeline_mask = torch.BoolTensor(log_seqs.cpu().numpy() == 0).to(self.dev)
        seqs *= ~timeline_mask.unsqueeze(-1)  # 廣播到最後一維

        # 4. Causal attention mask（因果遮蔽，防止未來資訊洩露）
        tl = seqs.shape[1]  # 序列長度
        attention_mask = ~torch.tril(torch.ones((tl, tl), dtype=torch.bool, device=self.dev))

        # 5. 透過Transformer塊
        for i in range(len(self.attention_layers)):
            # 自注意力子層（Pre-LN架構）
            seqs = torch.transpose(seqs, 0, 1)  # MultiheadAttention需要(seq_len, batch, embed)
            Q = self.attention_layernorms[i](seqs)
            mha_outputs, _ = self.attention_layers[i](
                Q, seqs, seqs,
                attn_mask=attention_mask
            )
            seqs = Q + mha_outputs  # 殘差連線
            seqs = torch.transpose(seqs, 0, 1)  # 轉回(batch, seq_len, embed)

            # FFN子層
            seqs = self.forward_layernorms[i](seqs)
            seqs = self.forward_layers[i](seqs)
            seqs *= ~timeline_mask.unsqueeze(-1)  # 重新應用padding mask

        # 6. 最終LayerNorm
        log_feats = self.last_layernorm(seqs)  # [batch_size, seq_len, hidden_units]

        return log_feats

    def forward(self, seqs, target, target_posi=None):
        """
        前向傳播：計算序列-物品匹配分數

        Args:
            seqs (Tensor): 使用者行為序列 [batch_size, seq_len]
            target (Tensor): 目標物品ID [batch_size] 或 [batch_size, K]
            target_posi (Tensor, optional): 目標位置索引 [N, 2]，格式為[batch_idx, seq_idx]

        Returns:
            scores (Tensor): 預測分數 [batch_size] 或 [N]
        """
        self._device()

        # 序列編碼
        log_feats = self.log2feats(seqs)

        # 提取序列表示
        if target_posi is not None:
            # 從指定位置提取特徵
            s_emb = log_feats[target_posi[:, 0], target_posi[:, 1]]
        else:
            # 預設使用最後一個時間步的特徵
            s_emb = log_feats[:, -1, :]

        # 目標物品嵌入
        target_embeds = self.item_emb(target.reshape(-1))

        # 計算匹配分數（內積）
        scores = torch.mul(s_emb, target_embeds).sum(dim=-1)

        return scores

    def forward_eval(self, user_ids, target_item, log_seqs):
        """
        評估時的前向傳播（僅使用最後一個時間步）

        Args:
            user_ids (Tensor): 使用者ID（未使用，保留介面相容性）
            target_item (Tensor): 目標物品ID [batch_size]
            log_seqs (Tensor): 使用者行為序列 [batch_size, seq_len]

        Returns:
            scores (Tensor): 預測分數 [batch_size]
        """
        self._device()
        log_feats = self.log2feats(log_seqs)

        # 使用最後一個時間步
        log_feats = log_feats[:, -1, :]
        item_embs = self.item_emb(target_item)

        return (log_feats * item_embs).sum(dim=-1)

    def computer(self):
        """
        相容介面：返回None（SASRec不使用預計算的使用者/物品表示）
        """
        return None, None

    def seq_encoder(self, seqs):
        """
        序列編碼器：將行為序列編碼為使用者表示

        Args:
            seqs (Tensor): 使用者行為序列 [batch_size, seq_len]

        Returns:
            seq_emb (Tensor): 序列嵌入（最後時間步） [batch_size, hidden_units]
        """
        self._device()
        log_feats = self.log2feats(seqs)
        seq_emb = log_feats[:, -1, :]
        return seq_emb

    def item_encoder(self, target_item, all_items=None):
        """
        物品編碼器：獲取物品嵌入

        Args:
            target_item (Tensor): 目標物品ID
            all_items: 未使用，保留介面相容性

        Returns:
            target_embeds (Tensor): 物品嵌入
        """
        self._device()
        target_embeds = self.item_emb(target_item)
        return target_embeds

    def predict(self, user_ids, log_seqs, item_indices):
        """
        預測介面：為指定物品列表計算分數

        Args:
            user_ids: 使用者ID（未使用）
            log_seqs (Tensor): 使用者行為序列 [batch_size, seq_len]
            item_indices (Tensor): 物品ID列表 [num_items]

        Returns:
            logits (Tensor): 預測logits [batch_size, num_items]
        """
        log_feats = self.log2feats(log_seqs)

        final_feat = log_feats[:, -1, :]  # 使用最後一個QKV狀態

        item_embs = self.item_emb(torch.LongTensor(item_indices).to(self.dev))  # [num_items, hidden_units]

        logits = item_embs.matmul(final_feat.unsqueeze(-1)).squeeze(-1)

        return logits

    def predict_all(self, user_ids, log_seqs):
        """
        預測所有物品的分數

        Args:
            user_ids: 使用者ID（未使用）
            log_seqs (Tensor): 使用者行為序列 [batch_size, seq_len]

        Returns:
            logits (Tensor): 所有物品的預測logits [batch_size, item_num]
        """
        log_feats = self.log2feats(log_seqs)

        final_feat = log_feats[:, -1, :]  # 取最後時間步

        item_embs = self.item_emb.weight  # 所有物品的嵌入 [item_num, hidden_units]

        # 計算使用者表示與所有物品的匹配分數
        logits = torch.matmul(final_feat, item_embs.T)  # [batch_size, item_num]

        return logits

    def predict_all_batch(self, user_ids, log_seqs, batch_size=128):
        """
        批次預測所有物品（與predict_all功能相同，保留介面相容性）

        Args:
            user_ids: 使用者ID（未使用）
            log_seqs (Tensor): 使用者行為序列 [batch_size, seq_len]
            batch_size: 批大小（未使用）

        Returns:
            logits (Tensor): 所有物品的預測logits [batch_size, item_num]
        """
        log_feats = self.log2feats(log_seqs)
        final_feat = log_feats[:, -1, :]
        item_embs = self.item_emb.weight
        logits = torch.matmul(final_feat, item_embs.T)
        return logits

    def log2feats_v2(self, log_seqs, emb_replace=None):
        """
        序列編碼（支援嵌入替換）- 用於特殊場景

        Args:
            log_seqs: 使用者行為序列（可包含負數ID）
            emb_replace: 替換嵌入（用於負數ID位置）

        Returns:
            log_feats: 序列特徵
        """
        log_seqs = log_seqs + 0

        # 處理負數ID（作為特殊標記）
        emb_replace_idx = np.where(log_seqs < 0)
        log_seqs[emb_replace_idx] = 0
        seqs = self.item_emb(torch.LongTensor(log_seqs).to(self.dev)) + 0
        log_seqs[emb_replace_idx] = -1

        # 替換特殊位置的嵌入
        if emb_replace is not None:
            seqs[emb_replace_idx[0], emb_replace_idx[1]] = 0
            seqs[emb_replace_idx[0], emb_replace_idx[1]] += emb_replace

        seqs *= self.item_emb.embedding_dim ** 0.5
        positions = np.tile(np.array(range(log_seqs.shape[1])), [log_seqs.shape[0], 1])
        seqs += self.pos_emb(torch.LongTensor(positions).to(self.dev))
        seqs = self.emb_dropout(seqs)

        timeline_mask = torch.BoolTensor(log_seqs == 0).to(self.dev)
        seqs *= ~timeline_mask.unsqueeze(-1)

        tl = seqs.shape[1]
        attention_mask = ~torch.tril(torch.ones((tl, tl), dtype=torch.bool, device=self.dev))

        for i in range(len(self.attention_layers)):
            seqs = torch.transpose(seqs, 0, 1)
            Q = self.attention_layernorms[i](seqs)
            mha_outputs, _ = self.attention_layers[i](Q, seqs, seqs, attn_mask=attention_mask)
            seqs = Q + mha_outputs
            seqs = torch.transpose(seqs, 0, 1)

            seqs = self.forward_layernorms[i](seqs)
            seqs = self.forward_layers[i](seqs)
            seqs *= ~timeline_mask.unsqueeze(-1)

        log_feats = self.last_layernorm(seqs)
        return log_feats

    def predict_position(self, log_seqs, positions, emb_replace=None):
        """
        預測指定位置的物品（用於特殊訓練策略）

        Args:
            log_seqs: 使用者行為序列
            positions: 目標位置索引
            emb_replace: 替換嵌入

        Returns:
            logits: 預測logits
        """
        log_feats = self.log2feats_v2(log_seqs, emb_replace=emb_replace)

        final_feat = log_feats[np.arange(positions.shape[0]), positions]

        item_embs = self.item_emb.weight

        logits = torch.matmul(final_feat, item_embs.T)

        return logits
