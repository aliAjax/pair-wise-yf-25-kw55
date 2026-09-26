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

    def _invite_accepted(self, paper_id, reviewer_id):
        assignment_id = self.store.assign("chair", paper_id, reviewer_id)["id"]
        self.store.respond_assignment(reviewer_id, assignment_id, True)
        return assignment_id

    def _complete(self, paper_id, reviewer_id, score, text="这是一份满足最低字数要求的评审意见。"):
        assignment_id = self._invite_accepted(paper_id, reviewer_id)
        self.store.submit_review(reviewer_id, assignment_id, score, text)
        return assignment_id

    def test_disputed_paper_requires_third_review_before_decision(self):
        paper_id = self._paper()
        first = self._invite_accepted(paper_id, "r1")
        second = self._invite_accepted(paper_id, "r2")
        self.store.submit_review("r1", first, 2, "方法存在明显问题，实验也无法支持结论。")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "under_review")

        self.store.submit_review("r2", second, 4, "思路较有价值，实验结果也比较扎实。")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "disputed")

        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept", "平均分为 3，直接接收。")
        self.assertEqual(ctx.exception.code, "dispute_review_pending")

        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "dispute_review_required")

        pool = self.store.get_dispute_review_pool("chair", paper_id)
        self.assertEqual(pool["state"], "awaiting_invitation")
        self.assertEqual([item["reviewer_id"] for item in pool["eligible"]], ["r3", "r4"])
        reasons = {item["reviewer_id"]: item["reason_code"] for item in pool["excluded"]}
        self.assertEqual(reasons["r1"], "already_assigned")
        self.assertEqual(reasons["r2"], "already_assigned")

        invitation = self.store.invite_dispute_review("chair", paper_id, "r3")
        self.store.respond_assignment("r3", invitation["assignment_id"], True)
        self.store.submit_review("r3", invitation["assignment_id"], 3, "补充信息后，我认为适合小修。")

        pool = self.store.get_dispute_review_pool("chair", paper_id)
        self.assertEqual(pool["state"], "ready_for_decision")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "disputed")
        decision = self.store.decide("chair", paper_id, "minor_revision", "三份意见齐备后小修。")
        self.assertEqual(decision["decision"], "minor_revision")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "decided")

        actions = [item["action"] for item in self.store.history("chair", paper_id)]
        self.assertIn("dispute.detected", actions)
        self.assertIn("dispute.review_invite", actions)
        self.assertIn("dispute.third_review_ready", actions)

    def test_dispute_pool_explains_every_excluded_reviewer(self):
        paper_id = self._paper()
        other_papers = [self._paper(), self._paper(), self._paper()]
        self.store.add_conflict("chair", paper_id, "r3", "近期有共同项目合作")

        # r1、r2 完成争议评审；r4 因三篇其他论文的待处理邀请达到负载上限。
        self._complete(paper_id, "r1", 2, "方法存在明显问题，实验也无法支持结论。")
        self._complete(paper_id, "r2", 4, "思路较有价值，实验结果也比较扎实。")
        for other_paper in other_papers:
            assignment_id = self.store.assign("chair", other_paper, "r4")["id"]
            self.store.respond_assignment("r4", assignment_id, True)

        with self.assertRaises(BusinessError) as ctx:
            self.store.invite_dispute_review("chair", paper_id, "r4")
        self.assertEqual(ctx.exception.code, "reviewer_at_capacity")

        pool = self.store.get_dispute_review_pool("chair", paper_id)
        self.assertEqual(pool["eligible"], [])
        self.assertEqual(pool["state"], "awaiting_invitation")
        reasons = {item["reviewer_id"]: item["reason_code"] for item in pool["excluded"]}
        self.assertEqual(reasons, {
            "r1": "already_assigned",
            "r2": "already_assigned",
            "r3": "conflict_of_interest",
            "r4": "reviewer_at_capacity",
        })

    def test_declined_dispute_invitation_can_be_replaced(self):
        paper_id = self._paper()
        self._complete(paper_id, "r1", 1, "问题严重，不建议录用，需要更多说明。")
        self._complete(paper_id, "r2", 5, "贡献突出，证据充分，建议接收。")

        first = self.store.invite_dispute_review("chair", paper_id, "r3")
        self.store.respond_assignment("r3", first["assignment_id"], False)
        second = self.store.invite_dispute_review("chair", paper_id, "r4")
        self.store.respond_assignment("r4", second["assignment_id"], True)

        with self.assertRaises(BusinessError) as ctx:
            self.store.invite_dispute_review("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "dispute_invitation_pending")

        self.store.submit_review("r4", second["assignment_id"], 4, "我认为论文质量较好，可以接收。")
        self.store.decide("chair", paper_id, "accept", "第三份意见后决定接收。")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "decided")

    def test_non_disputed_papers_keep_original_two_review_flow(self):
        paper_id = self._paper()
        self._complete(paper_id, "r1", 2, "方法存在明显问题，实验也无法支持结论。")
        self._complete(paper_id, "r2", 3, "实验充分，但部分结论需要进一步解释。")
        self.assertEqual(self.store.get_paper("chair", paper_id)["status"], "under_review")
        decision = self.store.decide("chair", paper_id, "major_revision", "按普通流程大修。")
        self.assertEqual(decision["decision"], "major_revision")

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


if __name__ == "__main__":
    unittest.main()
