#!/usr/bin/env python3
"""
Proxmox cluster load analyzer & live-migration planner.

Đọc snapshot JSON từ /cluster/resources, tính tải từng node,
phát hiện quá tải, đề xuất kế hoạch migrate VM.

Sử dụng:
    python proxmox_balance.py <snapshot.json>
    python proxmox_balance.py <snapshot.json> --format json
    python proxmox_balance.py <snapshot.json> --cpu-threshold 85 --mem-threshold 90
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any


DEFAULT_CPU_THRESHOLD = 80.0
DEFAULT_MEM_THRESHOLD = 85.0
DEFAULT_OVERCOMMIT_THRESHOLD = 4.0
MIN_NODE_MEM_BYTES = 1 * 1024**3


@dataclass
class VM:
    vmid: int
    name: str
    node: str
    status: str
    vcpus: int
    cpu_usage: float
    mem_max: int
    mem_used: int
    # True khi vmid xuất hiện nhiều lần trong snapshot (vị trí thật của VM chưa rõ)
    ambiguous: bool = False

    @property
    def cpu_used_cores(self) -> float:
        return self.vcpus * self.cpu_usage

    @property
    def is_running(self) -> bool:
        return self.status == "running"


@dataclass
class Node:
    name: str
    status: str
    cpu_cores: int
    mem_total: int
    vms: list[VM] = field(default_factory=list)
    cpu_used_cores: float = 0.0
    mem_used: int = 0
    vcpu_allocated: int = 0
    is_overloaded: bool = False
    overload_reasons: list[str] = field(default_factory=list)
    is_eligible_target: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def cpu_usage_pct(self) -> float:
        return (self.cpu_used_cores / self.cpu_cores * 100) if self.cpu_cores else 0.0

    @property
    def mem_usage_pct(self) -> float:
        # mem_total quá nhỏ => dữ liệu nghi hỏng, không tính % (tránh số vô nghĩa)
        if self.mem_total < MIN_NODE_MEM_BYTES:
            return 0.0
        return self.mem_used / self.mem_total * 100

    @property
    def overcommit_ratio(self) -> float:
        return (self.vcpu_allocated / self.cpu_cores) if self.cpu_cores else 0.0


@dataclass
class NodeSnapshot:
    """Trạng thái bất biến của node tại một thời điểm (before/after migrate)."""
    name: str
    status: str
    cpu_cores: int
    mem_total: int
    cpu_used_cores: float
    mem_used: int
    vcpu_allocated: int
    is_overloaded: bool
    overload_reasons: list[str]
    is_eligible_target: bool
    warnings: list[str]

    @property
    def cpu_usage_pct(self) -> float:
        return (self.cpu_used_cores / self.cpu_cores * 100) if self.cpu_cores else 0.0

    @property
    def mem_usage_pct(self) -> float:
        if self.mem_total < MIN_NODE_MEM_BYTES:
            return 0.0
        return self.mem_used / self.mem_total * 100

    @property
    def overcommit_ratio(self) -> float:
        return (self.vcpu_allocated / self.cpu_cores) if self.cpu_cores else 0.0


@dataclass
class Migration:
    vmid: int
    name: str
    from_node: str
    to_node: str
    vcpus: int
    mem_used: int
    reason: str


def _warn(warnings: list[str], msg: str) -> None:
    warnings.append(msg)


def _as_int(v: Any, default: int = 0) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _as_float(v: Any, default: float = 0.0) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return default


def load_snapshot(path: str) -> tuple[dict[str, Node], list[VM], list[str]]:
    warnings: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"LỖI: không tìm thấy file {path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"LỖI: file JSON không hợp lệ ({path}): {e}")

    if not isinstance(raw, dict):
        raise SystemExit("LỖI: root JSON phải là object")

    nodes: dict[str, Node] = {}
    for i, n in enumerate(raw.get("nodes") or []):
        if not isinstance(n, dict):
            _warn(warnings, f"nodes[{i}]: không phải object, bỏ qua")
            continue
        name = n.get("node")
        if not isinstance(name, str) or not name:
            _warn(warnings, f"nodes[{i}]: thiếu 'node', bỏ qua")
            continue
        cpu_cores = _as_int(n.get("cpu_cores"))
        mem_total = _as_int(n.get("mem_total"))
        status = n.get("status", "unknown")

        node = Node(name=name, status=status, cpu_cores=cpu_cores, mem_total=mem_total)

        if cpu_cores <= 0:
            node.warnings.append(f"cpu_cores={cpu_cores} không hợp lệ")
            node.is_eligible_target = False
        if mem_total < MIN_NODE_MEM_BYTES:
            hint = ""
            if mem_total > 0 and mem_total % 1024 == 0 and mem_total >= 1024:
                hint = (
                    f" (nếu đơn vị là MiB thì {mem_total} MiB = "
                    f"{mem_total/1024:.0f} GiB -> có thể exporter xuất sai đơn vị)"
                )
            node.warnings.append(
                f"mem_total={mem_total} bytes quá nhỏ (<1GB) - dữ liệu nghi hỏng{hint}"
            )
            node.is_eligible_target = False
        if status != "online":
            node.is_eligible_target = False
            if status == "offline":
                node.warnings.append("node offline - không thể nhận VM")

        nodes[name] = node

    vms: list[VM] = []
    seen_idx: dict[int, int] = {}          # vmid -> vị trí trong vms
    seen_sig: dict[int, dict] = {}         # vmid -> nội dung bản ghi (bỏ trường node)
    for i, v in enumerate(raw.get("vms") or []):
        if not isinstance(v, dict):
            _warn(warnings, f"vms[{i}]: không phải object, bỏ qua")
            continue
        vmid = _as_int(v.get("vmid"), -1)
        if vmid < 0:
            _warn(warnings, f"vms[{i}]: thiếu/không hợp lệ 'vmid', bỏ qua")
            continue

        name = v.get("name", f"vmid-{vmid}")
        node_name = v.get("node")
        if not isinstance(node_name, str) or not node_name:
            _warn(warnings, f"vmid={vmid}: thiếu 'node', bỏ qua")
            continue

        vcpus_raw = v.get("vcpus")
        if not isinstance(vcpus_raw, int) or isinstance(vcpus_raw, bool) or vcpus_raw <= 0:
            _warn(warnings, f"vmid={vmid} ({name}): vcpus={vcpus_raw} không hợp lệ, bỏ qua")
            continue

        mem_used_raw = v.get("mem_used")
        if mem_used_raw is None:
            _warn(warnings, f"vmid={vmid} ({name}): mem_used=None, bỏ qua")
            continue
        mem_used = _as_int(mem_used_raw, -1)
        if mem_used < 0:
            _warn(warnings, f"vmid={vmid} ({name}): mem_used không hợp lệ, bỏ qua")
            continue

        mem_max = _as_int(v.get("mem_max"), 0)

        cpu_usage = _as_float(v.get("cpu_usage"), -1.0)
        if cpu_usage < 0:
            _warn(warnings, f"vmid={vmid} ({name}): cpu_usage không hợp lệ, bỏ qua")
            continue
        if cpu_usage > 1.0:
            if cpu_usage <= 100.0:
                _warn(
                    warnings,
                    f"vmid={vmid} ({name}): cpu_usage={cpu_usage} ngoài [0,1] "
                    f"-> giả định đơn vị %, chia 100",
                )
                cpu_usage = cpu_usage / 100.0
            else:
                _warn(
                    warnings,
                    f"vmid={vmid} ({name}): cpu_usage={cpu_usage} > 100, clamp về 1.0",
                )
                cpu_usage = 1.0

        if mem_max and mem_used > mem_max:
            _warn(
                warnings,
                f"vmid={vmid} ({name}): mem_used({mem_used}) > mem_max({mem_max}), "
                f"clamp mem_used về mem_max",
            )
            mem_used = mem_max

        vm = VM(
            vmid=vmid, name=name, node=node_name,
            status=v.get("status", "unknown"),
            vcpus=vcpus_raw, cpu_usage=cpu_usage,
            mem_max=mem_max, mem_used=mem_used,
        )

        # vmid là duy nhất trong cluster: một VM chỉ nằm trên MỘT node.
        # Trùng vmid (snapshot lấy lúc đang migrate, bản ghi cũ, export lỗi...)
        # -> chỉ tính 1 lần, cảnh báo, và không đề xuất migrate VM này.
        sig = {k: val for k, val in v.items() if k != "node"}
        if vmid in seen_idx:
            kept = vms[seen_idx[vmid]]
            identical = sig == seen_sig[vmid]
            # ưu tiên bản ghi đang running; cùng trạng thái thì giữ bản xuất hiện trước
            if vm.is_running and not kept.is_running:
                vms[seen_idx[vmid]] = vm
                seen_sig[vmid] = sig
                keep, drop = vm, kept
            else:
                keep, drop = kept, vm
            keep.ambiguous = True
            _warn(
                warnings,
                f"vmid={vmid} ({name}): xuất hiện nhiều lần (node {kept.node} và "
                f"{vm.node}), nội dung {'giống hệt' if identical else 'KHÁC NHAU'}; "
                f"chỉ tính 1 lần theo bản ghi ở node {keep.node}, bỏ bản ghi ở node "
                f"{drop.node}, không đề xuất migrate VM này",
            )
            continue
        seen_idx[vmid] = len(vms)
        seen_sig[vmid] = sig
        vms.append(vm)

        if node_name not in nodes:
            _warn(
                warnings,
                f"vmid={vmid} ({name}): node='{node_name}' không có trong danh sách nodes",
            )

    for vm in vms:
        node = nodes.get(vm.node)
        if not node or not vm.is_running:
            continue
        node.vms.append(vm)
        node.cpu_used_cores += vm.cpu_used_cores
        node.mem_used += vm.mem_used
        node.vcpu_allocated += vm.vcpus

    return nodes, vms, warnings


def analyze(nodes: dict[str, Node], cpu_thr: float, mem_thr: float,
            overcommit_thr: float) -> None:
    for node in nodes.values():
        # bỏ qua node offline hoặc có dữ liệu nghi hỏng (RAM < 1GB, cores=0)
        if node.status != "online" or not node.is_eligible_target:
            continue
        if node.cpu_usage_pct > cpu_thr:
            node.is_overloaded = True
            node.overload_reasons.append(
                f"CPU {node.cpu_usage_pct:.1f}% > {cpu_thr:.0f}%"
            )
        if node.mem_usage_pct > mem_thr:
            node.is_overloaded = True
            node.overload_reasons.append(
                f"RAM {node.mem_usage_pct:.1f}% > {mem_thr:.0f}%"
            )
        if node.overcommit_ratio > overcommit_thr:
            node.is_overloaded = True
            node.overload_reasons.append(
                f"vCPU overcommit {node.overcommit_ratio:.2f}x > {overcommit_thr:.1f}x"
            )


def _recompute_overload(node: Node, cpu_thr: float, mem_thr: float,
                       overcommit_thr: float) -> None:
    """Tính lại is_overloaded + overload_reasons từ trạng thái hiện tại."""
    node.overload_reasons = []
    node.is_overloaded = False
    if node.cpu_usage_pct > cpu_thr:
        node.is_overloaded = True
        node.overload_reasons.append(
            f"CPU {node.cpu_usage_pct:.1f}% > {cpu_thr:.0f}%"
        )
    if node.mem_usage_pct > mem_thr:
        node.is_overloaded = True
        node.overload_reasons.append(
            f"RAM {node.mem_usage_pct:.1f}% > {mem_thr:.0f}%"
        )
    if node.overcommit_ratio > overcommit_thr:
        node.is_overloaded = True
        node.overload_reasons.append(
            f"vCPU overcommit {node.overcommit_ratio:.2f}x > {overcommit_thr:.1f}x"
        )


def snapshot_nodes(nodes: dict[str, Node]) -> dict[str, NodeSnapshot]:
    """Chụp lại trạng thái hiện tại của các node."""
    return {
        n.name: NodeSnapshot(
            name=n.name, status=n.status, cpu_cores=n.cpu_cores,
            mem_total=n.mem_total, cpu_used_cores=n.cpu_used_cores,
            mem_used=n.mem_used, vcpu_allocated=n.vcpu_allocated,
            is_overloaded=n.is_overloaded,
            overload_reasons=list(n.overload_reasons),
            is_eligible_target=n.is_eligible_target,
            warnings=list(n.warnings),
        )
        for n in nodes.values()
    }


def _would_stay_healthy(node: Node, vm: VM, cpu_thr: float,
                        mem_thr: float, overcommit_thr: float) -> bool:
    new_cpu = (node.cpu_used_cores + vm.cpu_used_cores) / node.cpu_cores * 100
    new_mem = (node.mem_used + vm.mem_used) / node.mem_total * 100
    new_oc = (node.vcpu_allocated + vm.vcpus) / node.cpu_cores
    return new_cpu <= cpu_thr and new_mem <= mem_thr and new_oc <= overcommit_thr


def _apply_migration(source: Node, target: Node, vm: VM) -> None:
    source.cpu_used_cores -= vm.cpu_used_cores
    source.mem_used -= vm.mem_used
    source.vcpu_allocated -= vm.vcpus
    source.vms = [v for v in source.vms if v.vmid != vm.vmid]

    target.cpu_used_cores += vm.cpu_used_cores
    target.mem_used += vm.mem_used
    target.vcpu_allocated += vm.vcpus
    target.vms.append(vm)


def plan_migrations(nodes: dict[str, Node], cpu_thr: float, mem_thr: float,
                    overcommit_thr: float) -> list[Migration]:
    migrations: list[Migration] = []
    # chỉ node quá tải thực sự (eligible) mới cần migrate;
    # node dữ liệu hỏng đã được cảnh báo riêng, không đưa vào kế hoạch
    overloaded = sorted(
        [n for n in nodes.values()
         if n.is_overloaded and n.is_eligible_target],
        key=lambda n: n.mem_usage_pct, reverse=True,
    )

    for src in overloaded:
        # VM trùng vmid (vị trí chưa rõ) không được đề xuất migrate
        candidates = sorted((v for v in src.vms if not v.ambiguous),
                            key=lambda v: v.mem_used, reverse=True)
        for vm in candidates:
            if not src.is_overloaded:
                break
            targets = [
                n for n in nodes.values()
                if n.name != src.name
                and n.is_eligible_target
                and not n.is_overloaded
                and _would_stay_healthy(n, vm, cpu_thr, mem_thr, overcommit_thr)
            ]
            if not targets:
                continue
            target = min(targets, key=lambda n: n.mem_usage_pct)
            migrations.append(Migration(
                vmid=vm.vmid, name=vm.name, from_node=src.name,
                to_node=target.name, vcpus=vm.vcpus, mem_used=vm.mem_used,
                reason=f"giải phóng {vm.mem_used/1e9:.1f}GB khỏi node quá tải",
            ))
            _apply_migration(src, target, vm)
            _recompute_overload(src, cpu_thr, mem_thr, overcommit_thr)

    # recalc toàn bộ node quá tải (kể cả node không migrate được)
    for n in nodes.values():
        if n.is_overloaded or n.overload_reasons:
            _recompute_overload(n, cpu_thr, mem_thr, overcommit_thr)
    return migrations


def _fmt_gb(b: int) -> str:
    return f"{b / 1e9:.2f}GB"


def print_table(before: dict[str, NodeSnapshot], after: dict[str, NodeSnapshot],
                migrations: list[Migration], warnings: list[str]) -> None:
    print("\n=== TẢI TỪNG NODE (TRƯỚC MIGRATE) ===")
    header = (f"{'NODE':14s} {'STATUS':8s} {'CPU':>9s} {'CPU%':>7s} "
              f"{'MEM':>19s} {'MEM%':>7s} {'vCPU':>5s} {'OC':>5s}  TÌNH TRẠNG")
    print(header)
    print("-" * len(header))
    for n in before.values():
        if not n.is_eligible_target and n.status == "online":
            # node có dữ liệu nghi hỏng (RAM < 1GB, cores=0) - không tin số liệu
            cpu_bar = "n/a"
            mem_bar = "n/a"
            cpu_pct = "n/a"
            mem_pct = "n/a"
            oc = "n/a"
            status = "DỮ LIỆU HỎNG"
            flags = "; ".join(n.warnings)
        else:
            cpu_bar = f"{n.cpu_used_cores:.1f}/{n.cpu_cores}"
            mem_bar = f"{_fmt_gb(n.mem_used)}/{_fmt_gb(n.mem_total)}"
            oc = f"{n.overcommit_ratio:.2f}x"
            status = "QUÁ TẢI" if n.is_overloaded else ("offline" if n.status != "online" else "ok")
            flags = " ".join(n.overload_reasons) if n.overload_reasons else ""
            cpu_pct = f"{n.cpu_usage_pct:.1f}%"
            mem_pct = f"{n.mem_usage_pct:.1f}%"
        print(f"{n.name:14s} {n.status:8s} {cpu_bar:>9s} {cpu_pct:>7s} "
              f"{mem_bar:>19s} {mem_pct:>7s} {n.vcpu_allocated:>5d} "
              f"{oc:>5s}  {status:12s} {flags}")

    print("\n=== CẢNH BÁO DỮ LIỆU ===")
    node_warns = [(n.name, w) for n in before.values() for w in n.warnings]
    if not warnings and not node_warns:
        print("  (không có)")
    else:
        for nname, w in node_warns:
            print(f"  [!] node {nname}: {w}")
        for w in warnings:
            print(f"  [!] {w}")

    print("\n=== KẾ HOẠCH MIGRATE ===")
    if not migrations:
        print("  Không cần migrate - cluster đã cân bằng.")
    else:
        print(f"  Đề xuất {len(migrations)} migration:")
        for m in migrations:
            print(f"  - vmid={m.vmid} ({m.name}) {m.from_node} -> {m.to_node} "
                  f"[{m.vcpus}vCPU, {_fmt_gb(m.mem_used)}] - {m.reason}")

    print("\n=== TẢI SAU MIGRATE (dự kiến) ===")
    for n in after.values():
        if n.status != "online":
            continue
        if not n.is_eligible_target:
            print(f"  {n.name}: n/a (dữ liệu nghi hỏng, cần kiểm tra thủ công)")
            continue
        print(f"  {n.name}: CPU {n.cpu_usage_pct:.1f}% | "
              f"RAM {n.mem_usage_pct:.1f}% | OC {n.overcommit_ratio:.2f}x")


def _node_snapshot_to_dict(n: NodeSnapshot) -> dict:
    return {
        "name": n.name,
        "status": n.status,
        "cpu_cores": n.cpu_cores,
        "mem_total": n.mem_total,
        "cpu_used_cores": round(n.cpu_used_cores, 3),
        "cpu_usage_pct": (
            round(n.cpu_usage_pct, 2) if n.is_eligible_target else None
        ),
        "mem_used": n.mem_used,
        "mem_usage_pct": (
            round(n.mem_usage_pct, 2) if n.is_eligible_target else None
        ),
        "vcpu_allocated": n.vcpu_allocated,
        "overcommit_ratio": round(n.overcommit_ratio, 3),
        "is_overloaded": n.is_overloaded,
        "is_eligible_target": n.is_eligible_target,
        "overload_reasons": n.overload_reasons,
        "warnings": n.warnings,
    }


def build_json_output(before: dict[str, NodeSnapshot],
                      after: dict[str, NodeSnapshot],
                      migrations: list[Migration],
                      warnings: list[str]) -> dict:
    return {
        "summary": {
            "total_nodes": len(before),
            "online_nodes": sum(1 for n in before.values() if n.status == "online"),
            "overloaded_nodes": [n.name for n in before.values() if n.is_overloaded],
            "migration_count": len(migrations),
            "warning_count": len(warnings),
        },
        "nodes_before": [_node_snapshot_to_dict(n) for n in before.values()],
        "nodes_after": [_node_snapshot_to_dict(n) for n in after.values()],
        "migrations": [
            {
                "vmid": m.vmid, "name": m.name,
                "from": m.from_node, "to": m.to_node,
                "vcpus": m.vcpus, "mem_used": m.mem_used, "reason": m.reason,
            }
            for m in migrations
        ],
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Phân tích tải cluster Proxmox & đề xuất live-migration.",
    )
    p.add_argument("snapshot", help="Đường dẫn file JSON snapshot")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--cpu-threshold", type=float, default=DEFAULT_CPU_THRESHOLD,
                   help=f"Ngưỡng phần trăm CPU (mặc định {DEFAULT_CPU_THRESHOLD})")
    p.add_argument("--mem-threshold", type=float, default=DEFAULT_MEM_THRESHOLD,
                   help=f"Ngưỡng phần trăm RAM (mặc định {DEFAULT_MEM_THRESHOLD})")
    p.add_argument("--overcommit-threshold", type=float,
                   default=DEFAULT_OVERCOMMIT_THRESHOLD,
                   help=f"Ngưỡng overcommit vCPU (mặc định {DEFAULT_OVERCOMMIT_THRESHOLD})")
    args = p.parse_args(argv)

    nodes, _vms, warnings = load_snapshot(args.snapshot)
    analyze(nodes, args.cpu_threshold, args.mem_threshold,
            args.overcommit_threshold)

    before = snapshot_nodes(nodes)

    migrations = plan_migrations(nodes, args.cpu_threshold, args.mem_threshold,
                                 args.overcommit_threshold)

    after = snapshot_nodes(nodes)

    migrated_from = {m.from_node for m in migrations}
    for name, snap in before.items():
        if snap.is_overloaded and name not in migrated_from:
            warnings.append(
                f"node {name}: quá tải ({'; '.join(snap.overload_reasons)}) "
                f"nhưng không tìm được node đích phù hợp để migrate"
            )

    if args.format == "json":
        json.dump(build_json_output(before, after, migrations, warnings),
                  sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print_table(before, after, migrations, warnings)
    return 0


if __name__ == "__main__":
    # Windows console mặc định cp1252 -> ép UTF-8 để in tiếng Việt + JSON
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
