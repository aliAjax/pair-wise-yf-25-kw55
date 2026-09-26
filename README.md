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

演示用户：`alice`、`bob`（作者），`r1`、`r2`、`r3`、`r4`（评审人），`chair`（主席）。所有 API 请求应带 `X-User-Id` 请求头。

- `POST /api/papers`：提交论文。
- `GET /api/papers` / `GET /api/papers/{id}`：按角色隔离查看；评审人看到双盲视图。
- `POST /api/papers/{id}/bids`：评审意向。
- `POST /api/papers/{id}/conflicts`：主席登记利益冲突。
- `POST /api/papers/{id}/assignments`：主席邀请评审人，执行负载上限与冲突检查。
- `POST /api/assignments/{id}/respond`：接受或拒绝邀请。
- `POST /api/assignments/{id}/review`：提交 1-5 分评审。
- `GET /api/papers/{id}/dispute/review-candidates`：主席查看争议复核候选池及每位评审人的排除原因。
- `POST /api/papers/{id}/dispute/invitation`：主席为争议论文邀请一位复核评审人。
- `POST /api/papers/{id}/rebuttal`：作者提交一次 Rebuttal。
- `POST /api/papers/{id}/decision`：收到至少两份评审后作决定。
- `GET /api/papers/{id}/history`：审计历史。

## 业务不变量

评审人不能查看未分配论文的作者身份；利益冲突禁止投标和分配；邀请和完成状态不能跳步；每位评审人的未完成分配受 `load_limit` 限制；每篇论文只能提交一次 Rebuttal；决定必须至少基于两份已完成评审。

### 争议复核

同一篇论文的已完成评审中同时出现不高于 2 分和不低于 4 分时，系统会把论文从 `under_review` 自动置为 `disputed`，并记录 `dispute.detected` 审计事件。争议期间不能使用普通分配接口，也不能只按前两份意见的平均分决定；必须先收到第三份复核意见。

主席通过独立的候选池接口查看 `eligible` 与 `excluded`：候选人必须没有利益冲突、没有该论文的邀请/接受/拒绝/完成记录，且未达到负载上限。没有合格候选人时，论文保持 `disputed`，候选池逐人说明排除原因。复核邀请由独立接口创建，第三份意见提交后论文才进入可决定状态；无争议论文仍按原来的两份评审流程处理。
