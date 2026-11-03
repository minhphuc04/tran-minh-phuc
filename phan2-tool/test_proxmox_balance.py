#!/usr/bin/env python3
"""Unit test cho proxmox_balance.py"""
import json
import tempfile
import unittest

from proxmox_balance import (
    Node, VM, analyze, load_snapshot, plan_migrations,
    _would_stay_healthy, build_json_output, snapshot_nodes,
)

GB = 1024**3


def _write_json(data: dict) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8")
    json.dump(data, f)
    f.close()
    return f.name


def _node(name="n1", cores=64, mem=100 * GB, status="online"):
    return {"node": name, "status": status, "cpu_cores": cores,
            "mem_total": mem}


def _vm(vmid=1, name="vm", node="n1", vcpus=4, cpu=0.5,
        mem_used=10 * GB, mem_max=20 * GB, status="running"):
    return {"vmid": vmid, "name": name, "node": node, "status": status,
            "vcpus": vcpus, "cpu_usage": cpu,
            "mem_used": mem_used, "mem_max": mem_max}


class TestLoadSnapshot(unittest.TestCase):

    def test_load_valid(self):
        path = _write_json({
            "nodes": [_node("n1"), _node("n2")],
            "vms": [_vm(1, node="n1"), _vm(2, node="n2")],
        })
        nodes, vms, warnings = load_snapshot(path)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(vms), 2)
        self.assertEqual(warnings, [])

    def test_invalid_json_raises(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write("{not json")
        f.close()
        with self.assertRaises(SystemExit):
            load_snapshot(f.name)

    def test_missing_file_raises(self):
        with self.assertRaises(SystemExit):
            load_snapshot("/tmp/does-not-exist-xyz.json")

    def test_skip_vm_missing_mem_used(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [
                _vm(1, mem_used=1 * GB),
                {"vmid": 2, "name": "broken", "node": "n1",
                 "status": "running", "vcpus": 2, "cpu_usage": 0.1,
                 "mem_used": None, "mem_max": GB},
            ],
        })
        nodes, vms, warnings = load_snapshot(path)
        self.assertEqual(len(vms), 1)
        self.assertTrue(any("mem_used=None" in w for w in warnings))

    def test_skip_vm_invalid_vcpus(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [{"vmid": 1, "name": "x", "node": "n1",
                     "status": "running", "vcpus": 0, "cpu_usage": 0.1,
                     "mem_used": 1, "mem_max": 2}],
        })
        _, vms, _ = load_snapshot(path)
        self.assertEqual(len(vms), 0)

    def test_cpu_usage_percent_converted(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [_vm(1, cpu=98.0)],  # 98% thay vì 0.98
        })
        _, vms, warnings = load_snapshot(path)
        self.assertAlmostEqual(vms[0].cpu_usage, 0.98)
        self.assertTrue(any("98.0" in w for w in warnings))

    def test_cpu_usage_above_100_clamped(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [_vm(1, cpu=150.0)],
        })
        _, vms, warnings = load_snapshot(path)
        self.assertEqual(vms[0].cpu_usage, 1.0)
        self.assertTrue(any("clamp" in w for w in warnings))

    def test_mem_used_clamped_to_max(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [{"vmid": 1, "name": "x", "node": "n1",
                     "status": "running", "vcpus": 2, "cpu_usage": 0.1,
                     "mem_used": 100, "mem_max": 50}],
        })
        _, vms, warnings = load_snapshot(path)
        self.assertEqual(vms[0].mem_used, 50)
        self.assertTrue(any("clamp" in w for w in warnings))

    def test_orphan_node_warning(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [_vm(1, node="n-ghost")],
        })
        _, _, warnings = load_snapshot(path)
        self.assertTrue(any("n-ghost" in w for w in warnings))

    def test_stopped_vm_not_counted_in_load(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [_vm(1, mem_used=10 * GB, status="stopped")],
        })
        nodes, _, _ = load_snapshot(path)
        self.assertEqual(nodes["n1"].mem_used, 0)
        self.assertEqual(nodes["n1"].vcpu_allocated, 0)

    def test_tiny_mem_node_not_eligible(self):
        """Node có mem_total < 1GB (dữ liệu hỏng) không được làm target."""
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB),
                      _node("n2", mem=262144)],  # ~256KB
            "vms": [],
        })
        nodes, _, _ = load_snapshot(path)
        self.assertFalse(nodes["n2"].is_eligible_target)
        self.assertTrue(any("quá nhỏ" in w for w in nodes["n2"].warnings))

    def test_offline_node_not_eligible(self):
        path = _write_json({
            "nodes": [_node("n1", status="offline")],
            "vms": [],
        })
        nodes, _, _ = load_snapshot(path)
        self.assertFalse(nodes["n1"].is_eligible_target)


class TestVmidHandling(unittest.TestCase):
    """Bản ghi thiếu vmid và vmid trùng lặp giữa các node."""

    def test_skip_vm_missing_vmid(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [{"name": "no_vmid", "node": "n1", "status": "running",
                     "vcpus": 2, "cpu_usage": 0.1,
                     "mem_used": 1, "mem_max": 2}],
        })
        _, vms, warnings = load_snapshot(path)
        self.assertEqual(len(vms), 0)
        self.assertTrue(any("vmid" in w for w in warnings))

    def test_duplicate_vmid_counted_once(self):
        """Cùng vmid trên 2 node: chỉ tính 1 lần, cảnh báo, đánh dấu mơ hồ."""
        path = _write_json({
            "nodes": [_node("n1"), _node("n2")],
            "vms": [_vm(1, node="n1", vcpus=4, mem_used=10 * GB),
                    _vm(1, node="n2", vcpus=4, mem_used=10 * GB)],
        })
        nodes, vms, warnings = load_snapshot(path)
        self.assertEqual(len(vms), 1)
        self.assertEqual(nodes["n1"].mem_used, 10 * GB)
        self.assertEqual(nodes["n2"].mem_used, 0)
        self.assertEqual(nodes["n2"].vcpu_allocated, 0)
        # tổng toàn cluster chỉ bằng 1 VM, không bị cộng đôi
        self.assertEqual(sum(n.mem_used for n in nodes.values()), 10 * GB)
        self.assertEqual(sum(n.vcpu_allocated for n in nodes.values()), 4)
        dup = [w for w in warnings if "nhiều lần" in w]
        self.assertEqual(len(dup), 1)
        self.assertIn("vmid=1", dup[0])
        self.assertIn("giống hệt", dup[0])
        self.assertTrue(vms[0].ambiguous)

    def test_duplicate_vmid_different_content_warns(self):
        path = _write_json({
            "nodes": [_node("n1"), _node("n2")],
            "vms": [_vm(1, node="n1", mem_used=10 * GB),
                    _vm(1, node="n2", mem_used=12 * GB)],
        })
        nodes, _, warnings = load_snapshot(path)
        self.assertTrue(any("KHÁC NHAU" in w for w in warnings))
        # vẫn giữ bản ghi xuất hiện trước
        self.assertEqual(nodes["n1"].mem_used, 10 * GB)
        self.assertEqual(nodes["n2"].mem_used, 0)

    def test_duplicate_prefers_running_record(self):
        """Nếu một bản ghi stopped, một running: giữ bản running."""
        path = _write_json({
            "nodes": [_node("n1"), _node("n2")],
            "vms": [_vm(1, node="n1", mem_used=10 * GB, status="stopped"),
                    _vm(1, node="n2", mem_used=10 * GB, status="running")],
        })
        nodes, vms, _ = load_snapshot(path)
        self.assertEqual(len(vms), 1)
        self.assertEqual(nodes["n1"].mem_used, 0)
        self.assertEqual(nodes["n2"].mem_used, 10 * GB)

    def test_duplicate_vmid_same_node_counted_once(self):
        path = _write_json({
            "nodes": [_node("n1")],
            "vms": [_vm(1, node="n1", mem_used=10 * GB),
                    _vm(1, node="n1", mem_used=10 * GB)],
        })
        nodes, vms, warnings = load_snapshot(path)
        self.assertEqual(len(vms), 1)
        self.assertEqual(nodes["n1"].mem_used, 10 * GB)
        self.assertTrue(any("nhiều lần" in w for w in warnings))

    def test_ambiguous_vm_never_migrated(self):
        """VM trùng vmid không được đề xuất migrate, dù là VM lớn nhất."""
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB), _node("n2", mem=100 * GB)],
            "vms": [
                _vm(1, node="n1", mem_used=60 * GB, mem_max=64 * GB),
                _vm(1, node="n2", mem_used=60 * GB, mem_max=64 * GB),  # bản trùng
                _vm(2, node="n1", mem_used=30 * GB, mem_max=64 * GB),  # n1 = 90%
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        self.assertTrue(nodes["n1"].is_overloaded)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        self.assertTrue(all(m.vmid != 1 for m in migrations))
        self.assertEqual([m.vmid for m in migrations], [2])
        self.assertFalse(nodes["n1"].is_overloaded)


class TestAnalyze(unittest.TestCase):

    def test_high_mem_marks_overloaded(self):
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB)],
            "vms": [_vm(1, mem_used=90 * GB, mem_max=100 * GB)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        self.assertTrue(nodes["n1"].is_overloaded)

    def test_high_cpu_marks_overloaded(self):
        path = _write_json({
            "nodes": [_node("n1", cores=8)],
            "vms": [_vm(1, vcpus=8, cpu=0.95)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        self.assertTrue(nodes["n1"].is_overloaded)

    def test_overcommit_marks_overloaded(self):
        path = _write_json({
            "nodes": [_node("n1", cores=4)],
            "vms": [_vm(1, vcpus=8), _vm(2, vcpus=8), _vm(3, vcpus=8)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        # 24 vcpu / 4 core = 6x > 4x
        self.assertTrue(nodes["n1"].is_overloaded)

    def test_tiny_mem_node_not_analyzed(self):
        """Node dữ liệu hỏng không bị đánh dấu quá tải (tránh nhiễu)."""
        path = _write_json({
            "nodes": [_node("n1", cores=32, mem=262144)],
            "vms": [_vm(1, mem_used=10 * GB)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        self.assertFalse(nodes["n1"].is_overloaded)
        self.assertEqual(nodes["n1"].mem_usage_pct, 0.0)


class TestPlanMigrations(unittest.TestCase):

    def test_offline_node_never_target(self):
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB),
                      _node("n2", mem=100 * GB, status="offline")],
            "vms": [_vm(1, node="n1", mem_used=95 * GB, mem_max=100 * GB)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        self.assertEqual(migrations, [])

    def test_tiny_mem_node_never_target(self):
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB),
                      _node("n2", cores=32, mem=262144)],
            "vms": [_vm(1, node="n1", mem_used=95 * GB, mem_max=100 * GB)],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        self.assertEqual(migrations, [])

    def test_tiny_mem_node_not_in_migration_source(self):
        """Node hỏng không được sinh migration (dù có VM)."""
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB),
                      _node("n2", cores=32, mem=262144)],
            "vms": [
                _vm(1, node="n2", mem_used=10 * GB),
                _vm(2, node="n1", mem_used=5 * GB),
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        # không có migration nào xuất phát từ n2
        self.assertFalse(any(m.from_node == "n2" for m in migrations))

    def test_migration_success(self):
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB), _node("n2", mem=100 * GB)],
            "vms": [
                _vm(1, node="n1", mem_used=50 * GB, mem_max=64 * GB),
                _vm(2, node="n1", mem_used=45 * GB, mem_max=64 * GB),  # n1 = 95% -> overload
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        self.assertGreater(len(migrations), 0)
        self.assertFalse(nodes["n1"].is_overloaded)
        self.assertLessEqual(nodes["n2"].mem_usage_pct, 85.0)

    def test_migration_does_not_overload_target(self):
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB), _node("n2", mem=100 * GB)],
            "vms": [
                _vm(1, node="n1", mem_used=90 * GB, mem_max=100 * GB),   # n1=90% overload
                _vm(2, node="n2", mem_used=80 * GB, mem_max=100 * GB),   # n2=80% không thể nhận
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        plan_migrations(nodes, 80, 85, 4.0)
        self.assertLessEqual(nodes["n2"].mem_usage_pct, 85.0)

    def test_overload_reasons_recomputed(self):
        """Sau migrate thành công, reasons của node nguồn phải rỗng."""
        # 2 VM nhỏ để migrate thực sự thành công sang n2
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB), _node("n2", mem=100 * GB)],
            "vms": [
                _vm(1, node="n1", mem_used=60 * GB, mem_max=64 * GB),
                _vm(2, node="n1", mem_used=30 * GB, mem_max=64 * GB),  # n1 = 90%
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        self.assertTrue(nodes["n1"].overload_reasons)
        plan_migrations(nodes, 80, 85, 4.0)
        self.assertEqual(nodes["n1"].overload_reasons, [])
        self.assertFalse(nodes["n1"].is_overloaded)

    def test_overload_reasons_kept_when_no_target(self):
        """Nếu không migrate được, reasons của node quá tải phải giữ nguyên."""
        path = _write_json({
            "nodes": [_node("n1", mem=100 * GB), _node("n2", mem=100 * GB)],
            "vms": [
                _vm(1, node="n1", mem_used=90 * GB, mem_max=100 * GB),
                _vm(2, node="n2", mem_used=80 * GB, mem_max=100 * GB),
            ],
        })
        nodes, _, _ = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        plan_migrations(nodes, 80, 85, 4.0)
        # VM 90GB không chuyển được sang n2 (sẽ thành 170% -> quá tải)
        self.assertTrue(nodes["n1"].is_overloaded)
        self.assertTrue(any("RAM 90.0%" in r for r in nodes["n1"].overload_reasons))


class TestWouldStayHealthy(unittest.TestCase):

    def test_accepts_within_thresholds(self):
        n = Node(name="n", status="online", cpu_cores=64, mem_total=100 * GB)
        vm = VM(vmid=1, name="v", node="n", status="running",
                vcpus=2, cpu_usage=0.5, mem_max=10 * GB, mem_used=10 * GB)
        self.assertTrue(_would_stay_healthy(n, vm, 80, 85, 4.0))

    def test_rejects_when_mem_over(self):
        n = Node(name="n", status="online", cpu_cores=64, mem_total=100 * GB)
        n.mem_used = 80 * GB
        vm = VM(vmid=1, name="v", node="n", status="running",
                vcpus=2, cpu_usage=0.5, mem_max=10 * GB, mem_used=10 * GB)
        self.assertFalse(_would_stay_healthy(n, vm, 80, 85, 4.0))


class TestJsonOutput(unittest.TestCase):

    def test_json_structure(self):
        path = _write_json({
            "nodes": [_node("n1"), _node("n2", mem=262144)],
            "vms": [_vm(1, node="n1")],
        })
        nodes, _, warnings = load_snapshot(path)
        analyze(nodes, 80, 85, 4.0)
        before = snapshot_nodes(nodes)
        migrations = plan_migrations(nodes, 80, 85, 4.0)
        after = snapshot_nodes(nodes)
        out = build_json_output(before, after, migrations, warnings)
        self.assertIn("summary", out)
        self.assertIn("nodes_before", out)
        self.assertIn("nodes_after", out)
        self.assertIn("migrations", out)
        self.assertIn("warnings", out)
        # node hỏng phải có pct = None
        n2 = [n for n in out["nodes_before"] if n["name"] == "n2"][0]
        self.assertIsNone(n2["cpu_usage_pct"])
        self.assertIsNone(n2["mem_usage_pct"])
        self.assertFalse(n2["is_eligible_target"])


if __name__ == "__main__":
    unittest.main()
