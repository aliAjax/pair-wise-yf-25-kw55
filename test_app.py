import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ReviewStore


class ReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def _paper(self):
        return self.store.submit_paper("alice", "可靠分布式提交协议", "本文提出一种用于弱网环境的可靠提交协议，并通过模拟实验验证其安全性和性能。")["id"]

    def test_complete_flow_and_double_blind_view(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 4, "方法严谨，缺少与最近工作的对比。")
        self.store.submit_review("r2", a2, 3, "实验充分，但部分结论需要进一步解释。")
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        result = self.store.decide("chair", paper_id, "minor_revision", "补充实验后接收。")
        self.assertEqual(result["decision"], "minor_revision")
        self.assertIsNone(self.store.get_paper("r1", paper_id)["author_id"])
        self.assertIsNotNone(self.store.get_paper("chair", paper_id)["author_id"])
        history = self.store.history("chair", paper_id)
        self.assertEqual(history[-1]["action"], "decision.record")
        self.assertGreaterEqual(len(history), 8)

    def test_conflict_blocks_assignment_and_role_is_enforced(self):
        paper_id = self._paper()
        self.store.add_conflict("chair", paper_id, "r1", "同一导师团队成员")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper_id, "r1")
        self.assertEqual(ctx.exception.code, "conflict_of_interest")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("alice", paper_id, "r2")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.get_paper("r2", paper_id)
        self.assertEqual(ctx.exception.status, 403)


class DisputeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def _paper_with_reviews(self, score1, score2):
        paper_id = self.store.submit_paper(
            "alice", "面向边缘计算的任务调度框架", "本文研究边缘计算场景下的任务调度问题，提出一种延迟感知的调度框架并验证其有效性。"
        )["id"]
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, score1, "第一份评审意见：对论文的创新性和实验设计给出评价。")
        return paper_id, self.store.submit_review("r2", a2, score2, "第二份评审意见：从另一角度评价论文质量与表达。")

    def _disputed_paper(self):
        return self._paper_with_reviews(2, 5)[0]

    def test_dispute_requires_third_review_before_decision(self):
        paper_id, result = self._paper_with_reviews(2, 5)
        self.assertEqual(result["paper_status"], "disputed")
        self.assertEqual(self.store.get_paper("alice", paper_id)["status"], "disputed")
        # 主席不能跳过复核直接决定，也不能走普通分配。
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept")
        self.assertEqual(ctx.exception.code, "dispute_pending")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "paper_disputed")
        # 候选人：r1、r2 已有任务被排除并说明原因，r3 合格。
        view = self.store.dispute_view("chair", paper_id)
        self.assertEqual(view["status"], "open")
        self.assertEqual((view["min_score"], view["max_score"]), (2, 5))
        by_id = {c["reviewer_id"]: c for c in view["candidates"]}
        self.assertIn("已有该论文的评审任务", by_id["r1"]["reason"])
        self.assertIn("已有该论文的评审任务", by_id["r2"]["reason"])
        self.assertTrue(by_id["r3"]["eligible"])
        self.assertIsNone(by_id["r3"]["reason"])
        self.assertEqual(view["eligible_count"], 1)
        with self.assertRaises(BusinessError) as ctx:
            self.store.dispute_view("alice", paper_id)
        self.assertEqual(ctx.exception.status, 403)
        # 主席追加复核评审人，第三份意见提交后争议解除，方可决定。
        a3 = self.store.invite_adjudicator("chair", paper_id, "r3")["id"]
        self.store.respond_assignment("r3", a3, True)
        result = self.store.submit_review("r3", a3, 4, "补充视角：方法有价值，但需要补充消融实验支撑结论。")
        self.assertEqual(result["paper_status"], "under_review")
        view = self.store.dispute_view("chair", paper_id)
        self.assertEqual(view["status"], "resolved")
        self.assertIsNotNone(view["resolved_at"])
        self.assertEqual(self.store.decide("chair", paper_id, "major_revision", "综合三份意见，需要大幅修改。")["decision"], "major_revision")
        actions = [h["action"] for h in self.store.history("chair", paper_id)]
        self.assertEqual(actions.count("dispute.open"), 1)
        self.assertIn("dispute.invite", actions)
        self.assertIn("dispute.resolve", actions)
        self.assertEqual(actions[-1], "decision.record")
        # 作者可以通过审计历史看到分歧处理过程。
        self.assertIn("dispute.open", [h["action"] for h in self.store.history("alice", paper_id)])

    def test_close_scores_follow_normal_flow(self):
        paper_id, result = self._paper_with_reviews(2, 3)
        self.assertEqual(result["paper_status"], "under_review")
        with self.assertRaises(BusinessError) as ctx:
            self.store.dispute_view("chair", paper_id)
        self.assertEqual(ctx.exception.code, "dispute_not_found")
        self.store.decide("chair", paper_id, "reject")

    def test_boundary_scores_do_not_open_dispute(self):
        _, result = self._paper_with_reviews(3, 4)
        self.assertEqual(result["paper_status"], "under_review")

    def test_no_eligible_candidate_keeps_dispute_with_reasons(self):
        paper_id = self._disputed_paper()
        self.store.add_conflict("chair", paper_id, "r3", "与作者共同发表过论文")
        view = self.store.dispute_view("chair", paper_id)
        self.assertEqual(view["eligible_count"], 0)
        reasons = {c["reviewer_id"]: c["reason"] for c in view["candidates"]}
        self.assertIn("利益冲突", reasons["r3"])
        self.assertTrue(all(reason is not None for reason in reasons.values()))
        # 无合格候选人：论文保持争议状态，不合格邀请被拒，决定仍被阻止。
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "disputed")
        with self.assertRaises(BusinessError) as ctx:
            self.store.invite_adjudicator("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "conflict_of_interest")
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "reject")
        self.assertEqual(ctx.exception.code, "dispute_pending")

    def test_capacity_excludes_candidate(self):
        paper_id = self._disputed_paper()
        for title in ("基于强化学习的资源编排策略研究", "云原生场景下的灰度发布机制"):
            other = self.store.submit_paper("bob", title, "这是一篇用于占用评审人负载的占位论文，摘要内容足够长以满足校验。")["id"]
            self.store.assign("chair", other, "r3")
        view = self.store.dispute_view("chair", paper_id)
        r3 = next(c for c in view["candidates"] if c["reviewer_id"] == "r3")
        self.assertFalse(r3["eligible"])
        self.assertIn("负载已满", r3["reason"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.invite_adjudicator("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "reviewer_at_capacity")

    def test_declined_adjudicator_keeps_dispute_open(self):
        paper_id = self._disputed_paper()
        a3 = self.store.invite_adjudicator("chair", paper_id, "r3")["id"]
        self.store.respond_assignment("r3", a3, False)
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "disputed")
        with self.assertRaises(BusinessError) as ctx:
            self.store.invite_adjudicator("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "assignment_exists")
        # 主席可另邀合格评审人完成复核。
        with self.store.connect() as conn:
            conn.execute("INSERT INTO users(id,name,role,load_limit) VALUES('r4','评审人四号','reviewer',3)")
        a4 = self.store.invite_adjudicator("chair", paper_id, "r4")["id"]
        self.store.respond_assignment("r4", a4, True)
        result = self.store.submit_review("r4", a4, 3, "综合看方法可行，但写作和实验都需要加强。")
        self.assertEqual(result["paper_status"], "under_review")
        self.store.decide("chair", paper_id, "major_revision")


if __name__ == "__main__":
    unittest.main()
