"""ZEN-019：核心立場的長期護欄（P1 / P4 / P5）與前端鐵則。

**這些不是「驗一次就算」的測試，它們要留在 repo 裡長期跑。**
每一條擋的是一個「加上去的時候完全沒有症狀」的改動：

  G-1  沙盒 env 多一個變數        → P4 的第一道關破了，而且沒有任何錯誤訊息
  G-2  出現「允許連內網」的開關   → 一個存在的旁路遲早會有人打開
  G-3  `/internal/*` 進了 OpenAPI → 把沙盒契約變成對外承諾
  G-4  新程式碼 exec 使用者模組   → P1
  G-5  三層綁到版本上            → 發新版要重填，改一次要改 N 次
  G-6  Console 用 innerHTML 寫 API 字串 → vanilla 最容易出的 XSS

全部是靜態／單元，不需要服務也不需要 docker。
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
APPS = REPO / "apps"
CONSOLE = REPO / "apps/console"

#: 這個 feature 新增或改動的檔案。護欄只掃這些，不掃整個 repo ——
#: 掃全部會因為既有程式碼而紅，而那時沒有人分得出是不是這次弄壞的。
NEW_BACKEND_FILES = [
    "apps/api/services/egress.py",
    "apps/api/services/egress_rules.py",
    "apps/api/services/egress_audit.py",
    "apps/api/services/agent_secrets.py",
    "apps/api/services/agent_settings.py",
    "apps/api/services/notify.py",
    "apps/api/migrations/versions/0008_agent_config_secrets_egress.py",
]
NEW_CONSOLE_FILES = [
    "apps/console/static/js/components/agent-config-card.js",
    "apps/console/static/js/components/agent-secrets-card.js",
    "apps/console/static/js/components/egress-card.js",
    "apps/console/static/js/components/egress-calls-panel.js",
]


# ── G-1：沙盒 env 的精確集合（Z19-51）────────────────────────────────

def test_sandbox_environment_is_an_exact_set_per_mode():
    """**這次不新增任何沙盒環境變數**（ADR-0014 選項 A 永久否決）。

    RUN 恰好三個、SERVE 恰好四個、VALIDATE 恰好三個。
    多一個少一個都要在建立容器時就失敗 —— 而不是等到有人發現金鑰進了沙盒。
    """
    from apps.sandbox.drivers.base import SandboxMode, SandboxSpec

    expected = {
        SandboxMode.RUN: {"ZENTRA_GATEWAY_URL", "ZENTRA_RUN_ID", "ZENTRA_RUN_TOKEN"},
        SandboxMode.SERVE: {"ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_ID",
                            "ZENTRA_CONTAINER_TOKEN", "ZENTRA_MAX_CONCURRENT_RUNS"},
        SandboxMode.VALIDATE: {"ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_VERSION_ID",
                               "ZENTRA_VALIDATION_TOKEN"},
    }
    source = (REPO / "apps/sandbox/drivers/base.py").read_text()
    tree = ast.parse(source)
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name == "_validate_environment"):
            continue
        for mapping in ast.walk(node):
            if not isinstance(mapping, ast.Dict):
                continue
            for key, value in zip(mapping.keys, mapping.values):
                if isinstance(key, ast.Attribute) and isinstance(value, ast.Set):
                    found[key.attr] = {
                        element.value for element in value.elts
                        if isinstance(element, ast.Constant)}
    assert found, "找不到 _validate_environment 的期望集合"
    for mode, names in expected.items():
        assert found.get(mode.name) == names, mode.name

    # 動態確認：多帶一個就要炸。
    # `run_id` 是 ZEN-000 的舊欄位，與 OCI 的 `resource_id` 不能並存。
    run_uuid = "01930000-0000-7000-8000-000000000001"
    good = {"ZENTRA_GATEWAY_URL": "http://app:8000/internal",
            "ZENTRA_RUN_ID": run_uuid, "ZENTRA_RUN_TOKEN": "t"}
    spec = SandboxSpec(
        mode=SandboxMode.RUN, resource_id=run_uuid,
        image_ref="localhost:6000/x@sha256:" + "a" * 64,
        env=dict(good), network="zentra-sandbox-net")
    assert set(spec.env) == expected[SandboxMode.RUN]

    for broken in ({**good, "SMTP_PASSWORD": "leaked"},
                   {key: value for key, value in good.items()
                    if key != "ZENTRA_RUN_TOKEN"}):
        with pytest.raises(ValueError):
            SandboxSpec(
                mode=SandboxMode.RUN, resource_id=run_uuid,
                image_ref="localhost:6000/x@sha256:" + "a" * 64,
                env=broken, network="zentra-sandbox-net")


def test_sandbox_package_was_not_touched_by_this_feature():
    """ZEN-019 spec §6：`apps/sandbox/**` 這次不需要改。

    會想改它幾乎一定是走上了「把設定塞進 env」那條被否決的路。
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "229c3f9..HEAD", "--", "apps/sandbox"],
            cwd=REPO, capture_output=True, text=True)
    except FileNotFoundError:
        pytest.skip("這個環境沒有 git（測試映像裡沒裝）")
    assert result.stdout.strip() == "", \
        f"ZEN-019 之後 apps/sandbox 被改過：\n{result.stdout}"


# ── G-2：沒有旁路（Z19-29 的前提 / 契約 §4.2 第 9 點）────────────────

#: 這些名字一出現在 `apps/**` 就是一個可以被打開的旁路。
#: **連「預設關閉」的都不行** —— 它打開時沒有任何症狀。
BYPASS_NAMES = [
    "ALLOW_PRIVATE", "allow_private",
    "ALLOW_INTERNAL", "allow_internal",
    "SKIP_DNS", "skip_dns",
    "SKIP_ADDRESS_CHECK", "skip_address_check",
    "ALLOW_PUBLIC_EGRESS", "allow_public_egress",
    "DISABLE_EGRESS", "disable_egress",
    "INSECURE_EGRESS", "insecure_egress",
    "ZENTRA_TEST_ALLOW_PUBLIC_EGRESS",
]


@pytest.mark.parametrize("name", BYPASS_NAMES)
def test_no_bypass_switch_exists_in_product_code(name: str):
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.yaml",
         name, str(APPS)],
        capture_output=True, text=True)
    assert result.stdout.strip() == "", (
        f"`{name}` 出現在 apps/** 裡。那是一個可以被打開的旁路，"
        f"而它打開時沒有任何症狀：\n{result.stdout}")


def test_the_three_mandatory_checks_have_no_conditional_escape():
    """scheme / redirect / 私有位址三項檢查**不吃任何設定值**。"""
    source = (REPO / "apps/api/services/egress.py").read_text()
    for marker in ('if parts.scheme != "https" or raw_scheme != "https":',
                   "if 300 <= response.status_code < 400:",
                   "unsafe = first_blocked_address(addresses)"):
        assert marker in source, marker
    # 三項檢查附近不得出現 settings.*（上限那幾個是另一回事，它們本來就可調）
    for line in source.splitlines():
        if "first_blocked_address" in line or "sni_hostname" in line:
            assert "settings" not in line, line


def test_the_injectable_seams_do_not_change_behaviour():
    """`resolve_addresses` 與 `build_transport` 是可替換的縫，**不是開關**。

    前者只做解析不做判斷，後者正式環境回 `None`（httpx 用預設 transport）。
    這條測試擋的是「有人為了方便在 build_transport 裡加一個 verify=False」。
    """
    from apps.api.services import egress

    assert egress.build_transport() is None
    source = (REPO / "apps/api/services/egress.py").read_text()
    body = source.split("def build_transport():")[1].split("def _authority")[0]
    assert "os.environ" not in body and "getenv" not in body
    assert "verify" not in source, "不得關掉 TLS 驗證"


# ── G-3：`/internal/*` 不進公開契約 ──────────────────────────────────

def test_internal_router_is_hidden_from_the_openapi_schema():
    from apps.api.routers import internal

    assert internal.router.include_in_schema is False
    for route in internal.router.routes:
        assert getattr(route, "include_in_schema", False) is False, route.path


# ── G-4：P1 —— 不載入、不執行使用者程式碼 ───────────────────────────

@pytest.mark.parametrize("relative", NEW_BACKEND_FILES)
def test_new_backend_code_never_runs_user_code(relative: str):
    source = (REPO / relative).read_text()
    for forbidden in ("exec(", "eval(", "importlib.import_module",
                      "__import__(", "subprocess", "pickle.loads"):
        assert forbidden not in source, f"{relative} 出現 {forbidden}"


def test_the_proxy_does_not_parse_or_execute_what_it_relays():
    """出口 proxy **只轉傳位元組**。它不解析上游回應，也不對它做任何事。"""
    source = (REPO / "apps/api/services/egress.py").read_text()
    assert "json.loads" not in source
    assert 'payload_bytes.decode("utf-8", errors="replace")' in source


# ── G-5：P5 —— 三層綁 Agent 不綁版本 ────────────────────────────────

def test_the_five_new_tables_have_no_version_column():
    from apps.api.models import (
        AgentEgressRule, AgentEmailSender, AgentSecret, AgentSettings, EgressCall,
    )

    for model in (AgentSettings, AgentSecret, AgentEgressRule, AgentEmailSender,
                  EgressCall):
        columns = set(model.__table__.columns.keys())
        assert "agent_version_id" not in columns, model.__tablename__
        assert "agent_id" in columns, model.__tablename__


def test_the_migration_can_be_downgraded():
    """Alembic 的 downgrade 要把五張表都拿掉。沒有 downgrade 的 migration
    在出事的時候是沒辦法回頭的。"""
    source = (REPO / "apps/api/migrations/versions/"
              "0008_agent_config_secrets_egress.py").read_text()
    downgrade = source.split("def downgrade()")[1]
    for table in ("egress_calls", "agent_email_sender", "agent_egress_rules",
                  "agent_secrets", "agent_settings"):
        assert f'drop_table("{table}")' in downgrade, table


def test_the_audit_table_keeps_rows_when_a_rule_is_deleted():
    """`egress_calls.rule_id` 是 `ON DELETE SET NULL`，**不是 CASCADE** ——
    刪一條白名單不該抹掉它過去的稽核紀錄。"""
    from apps.api.models import EgressCall

    foreign_keys = {fk.column.table.name: fk.ondelete
                    for fk in EgressCall.__table__.foreign_keys}
    assert foreign_keys["agent_egress_rules"] == "SET NULL"
    assert foreign_keys["runs"] == "CASCADE"
    assert foreign_keys["agents"] == "CASCADE"


# ── 契約一致性（Z19-56）─────────────────────────────────────────────

@pytest.mark.parametrize("relative", NEW_BACKEND_FILES + NEW_CONSOLE_FILES)
def test_no_removed_vocabulary_creeps_back(relative: str):
    """`WAITING_INPUT` 等等在 v0.2.0 就移除了，不要寫也不要斷言。"""
    source = (REPO / relative).read_text()
    for removed in ("WAITING_INPUT", "tool.call", "tool.result", "tenant_id"):
        assert removed not in source, f"{relative} 出現 {removed}"


def test_error_codes_come_from_the_enum():
    """錯誤 code 一律引用枚舉常數，不是散落的字面值。"""
    source = (REPO / "apps/api/services/egress.py").read_text()
    assert 'ErrorCode.EGRESS_BLOCKED' in source
    assert 'ErrorCode.EGRESS_FAILED' in source
    assert '"EGRESS_BLOCKED"' not in source
    assert '"EGRESS_FAILED"' not in source


# ── G-6：前端鐵則（Z19-61）──────────────────────────────────────────

@pytest.mark.parametrize("relative", NEW_CONSOLE_FILES)
def test_console_components_never_innerhtml_api_strings(relative: str):
    """`innerHTML` 只准寫一次**固定結構**（`root.innerHTML = FRAME`）。

    動態內容一律 `textContent` —— 這是 vanilla 最容易出的 XSS，
    而 config 的值與機密的名字完全是使用者輸入。
    """
    source = (REPO / relative).read_text()
    uses = re.findall(r"innerHTML\s*=\s*(\w+)", source)
    assert uses == ["FRAME"], f"{relative}: {uses}"
    assert "insertAdjacentHTML" not in source
    assert "outerHTML" not in source


@pytest.mark.parametrize("relative", NEW_CONSOLE_FILES)
def test_console_components_have_no_hardcoded_colours(relative: str):
    """只引用 ui-contract §1 的 CSS 變數。"""
    source = (REPO / relative).read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![\w-])", source), relative
    assert "rgb(" not in source and "rgba(" not in source


@pytest.mark.parametrize("relative", NEW_CONSOLE_FILES)
def test_console_components_import_nothing_but_local_modules(relative: str):
    """不准引入任何執行期函式庫（ADR-0006）。"""
    source = (REPO / relative).read_text()
    for match in re.findall(r"from\s+'([^']+)'", source):
        assert match.startswith("."), f"{relative} imports {match}"
    assert "require(" not in source


@pytest.mark.parametrize("relative", NEW_CONSOLE_FILES)
def test_console_components_can_be_destroyed(relative: str):
    """每個 `mount()` 都要能 `destroy()`，而且真的拆掉監聽。"""
    source = (REPO / relative).read_text()
    assert "export function mount(" in source
    assert "destroy()" in source
    added = source.count("addEventListener")
    removed = source.count("removeEventListener")
    assert removed >= added - 1, f"{relative}: add={added} remove={removed}"


def test_contract_field_names_only_appear_in_api_js():
    """`api.js` 是契約的唯一出口（ui-contract §6.2）。

    元件只拿 camelCase 的 view model —— snake_case 漏進元件就表示
    有人繞過了那一層，而那一層是沒有型別檢查時唯一的防線。
    """
    leaked = {
        "last_four", "auth_secret_name", "auth_header", "path_prefix",
        "password_secret_name", "allowed_recipients", "smtp_host", "smtp_port",
        "from_address", "status_code", "request_bytes", "response_bytes",
        "latency_ms", "next_cursor", "rule_id", "call_id", "run_id",
    }
    for relative in NEW_CONSOLE_FILES:
        source = (REPO / relative).read_text()
        # 註解裡提到契約欄位名是**好事**（「這一欄對應契約的哪一個」），
        # 要擋的是程式碼真的去讀它。所以先把註解拿掉。
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = "\n".join(line for line in code.splitlines()
                         if not line.lstrip().startswith("//"))
        # 表單欄位的 name="..." 與 id="..." 不算 —— 那是 DOM，不是契約欄位讀取。
        code = re.sub(r'(name|id|for|aria-describedby)="[^"]*"', "", code)
        code = re.sub(r"#[a-z-]+", "", code)
        for field in leaked:
            assert field not in code, f"{relative} 直接用了契約欄位名 {field}"
