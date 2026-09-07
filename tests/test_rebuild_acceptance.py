import subprocess
import sys
from pathlib import Path

from scripts.acceptance_scan import format_summary


def test_acceptance_summary_only_contains_safe_counts():
    text = format_summary(scan_id="abc", status="SUCCESS", departments=7, offers=178, models=69)
    assert text == "扫描ID: abc\n状态: SUCCESS\n网点数: 7\n报价数: 178\n车型数: 69"
    assert "cookie" not in text.lower()
    assert "payload" not in text.lower()


def test_acceptance_script_can_run_directly_from_project_root():
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "scripts/acceptance_scan.py", "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "执行一次神州车型雷达真实扫描" in completed.stdout


def test_readme_documents_citywide_states_and_retention():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "广州全城扫描" in readme
    assert "AVAILABLE" in readme
    assert "NOT_FOUND" in readme
    assert "INCOMPLETE" in readme
    assert "网点报价明细和压缩原始响应保留 60 天" in readme
    assert "不是最终结算价" in readme


def test_readme_documents_ai_enrichment_operation_and_recovery():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "OpenAI 兼容接口",
        "补全待处理车型",
        "重新识别全部车型",
        "停止、继续和重试失败",
        "未知不猜测",
        "Docker 重启",
        "Token",
        "清除密钥",
    ):
        assert phrase in readme


def test_readme_documents_model_search_sampling_limits_and_trust():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "按车型找车",
        "未来 4 个周末",
        "2000",
        "5000",
        "2 小时",
        "扫描不完整",
        "广州同城还车",
        "按车型找车任务、样本和报价保留 60 天",
    ):
        assert phrase in readme
