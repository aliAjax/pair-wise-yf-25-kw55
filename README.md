# 学术会议同行评审系统

一个仅使用 Python 3.11+ 标准库的独立示例项目。SQLite 保存数据，`http.server` 提供 JSON API 和演示页面。

## 运行

```bash
python app.py --init --seed
python app.py
```

访问 <http://127.0.0.1:8101>。默认数据库为 `review.db`，端口为 `8101`。测试：

```bash
python -m unittest -v
```

## 角色和主要接口

演示用户：`alice`、`bob`（作者），`r1`、`r2`、`r3`（评审人），`chair`（主席）。所有 API 请求应带 `X-User-Id` 请求头。

- `POST /api/papers`：提交论文。
- `GET /api/papers` / `GET /api/papers/{id}`：按角色隔离查看；评审人看到双盲视图。
- `POST /api/papers/{id}/bids`：评审意向。
- `POST /api/papers/{id}/conflicts`：主席登记利益冲突。
- `POST /api/papers/{id}/assignments`：主席邀请评审人，执行负载上限与冲突检查。
- `POST /api/assignments/{id}/respond`：接受或拒绝邀请。
- `POST /api/assignments/{id}/review`：提交 1-5 分评审。
- `POST /api/papers/{id}/rebuttal`：作者提交一次 Rebuttal。
- `GET /api/papers/{id}/dispute`：主席查看争议单与复核候选人（含每人被排除的原因）。
- `POST /api/papers/{id}/dispute/invite`：主席为争议论文追加一位复核评审人。
- `POST /api/papers/{id}/decision`：收到至少两份评审后作决定。
- `GET /api/papers/{id}/history`：审计历史。

## 业务不变量

评审人不能查看未分配论文的作者身份；利益冲突禁止投标和分配；邀请和完成状态不能跳步；每位评审人的未完成分配受 `load_limit` 限制；每篇论文只能提交一次 Rebuttal；决定必须至少基于两份已完成评审。

## 争议复核

分歧判定、复核邀请和主席操作三块逻辑分开维护：

1. **分歧判定**：前两份完成评审中同时出现 2 分及以下和 4 分及以上时，论文自动进入 `disputed` 争议状态（3/4 等边界组合不触发）。普通论文仍按原流程处理。
2. **复核邀请**：主席只能从无利益冲突、未满负载且尚未受邀的评审人中追加一位。候选人列表为每人标注是否合格及排除原因（利益冲突 / 已有任务 / 负载已满）；没有合格候选人时论文保持争议状态，`dispute_pending` 阻止任何决定。
3. **主席操作**：第三份意见提交后争议自动解除并回到 `under_review`，主席才能决定。复核邀请被拒绝后可另邀他人，但同一位评审人不能重复邀请。

争议的开启、邀请、解除均写入审计历史，作者可在 `history` 中看到分歧处理过程。
