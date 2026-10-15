#!/usr/bin/env python3
"""补丁后的真实构建器必须把 nftables 纳入配置和配置结果验证。"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", type=Path)
    args = ap.parse_args()
    tool = args.tool or Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sunuefi")) / "tools/build_piano_full_kernel.py"
    if not tool.is_file():
        print("SKIP: patched SunUEFI unavailable; Fedora kernel configuration not checked")
        return 0
    # 执行真实字典和验证函数，但不导入上游构建器的其他依赖或运行 main。
    tree = ast.parse(tool.read_text())
    selected = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef))
                or isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "update"]
    namespace = {"__file__": str(tool), "Path": Path, "json": json, "re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(tool), "exec"), namespace)
    required = {"CONFIG_NETFILTER", "CONFIG_NETFILTER_ADVANCED", "CONFIG_NFT_REJECT",
                "CONFIG_NFT_FIB", "CONFIG_NF_TABLES", "CONFIG_NF_TABLES_INET", "CONFIG_NF_CONNTRACK",
                "CONFIG_NF_NAT", "CONFIG_NFT_CT", "CONFIG_NFT_LOG", "CONFIG_NFT_LIMIT",
                "CONFIG_NFT_REJECT_INET", "CONFIG_NFT_FIB_INET", "CONFIG_NFT_FIB_IPV4",
                "CONFIG_NFT_FIB_IPV6", "CONFIG_NFT_MASQ", "CONFIG_NFT_NAT", "CONFIG_NFT_REDIR"}
    policy = namespace["FEDORA_OVERRIDES"]
    assert all(policy.get(symbol) == "y" for symbol in required), "Fedora nftables kernel options missing"
    validate = namespace["validate_module_overrides"]
    assert validate(policy) == policy
    incomplete = dict(policy)
    incomplete.pop("CONFIG_NF_TABLES")
    try:
        validate(incomplete)
    except ValueError:
        pass
    else:
        raise AssertionError("incomplete Fedora policy accepted")
    # 直接走构建器用于 olddefconfig 结果的生产验证路径，逐项模拟被 Kconfig 丢弃。
    command = "piano.root=LABEL=PIANOROOT rootfstype=f2fs"
    configured = {"CONFIG_" + key: value for key, value in namespace["REQUIRED"].items()}
    configured.update(policy)
    configured["CONFIG_CMDLINE"] = json.dumps(command)
    serialize = lambda values: "".join(key + "=" + value + "\n" for key, value in values.items())
    validate_config = namespace["validate_config"]
    validate_config(serialize(configured), "CONFIG_MODULES=y\n", command, policy)
    for symbol in required:
        dropped = dict(configured)
        dropped.pop(symbol)
        try:
            validate_config(serialize(dropped), "CONFIG_MODULES=y\n", command, policy)
        except ValueError as exc:
            assert symbol in str(exc), str(exc)
        else:
            raise AssertionError("olddefconfig dropped option accepted: " + symbol)
    # main 的有效分支必须将 Fedora 策略交给配置构造器；ALLOWED_OVERRIDES 不算消费。
    main_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    branches = [node for node in ast.walk(main_node) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Attribute) and node.test.attr == "fedora_policy"]
    assert len(branches) == 1 and any(isinstance(node, ast.Name) and node.id == "FEDORA_OVERRIDES"
                                    for node in ast.walk(branches[0])), "main does not consume Fedora policy"
    gunyah = namespace["GUNYAH_OVERRIDES"]
    assert gunyah == {"CONFIG_GUNYAH": "y", "CONFIG_GUNYAH_HYPERCALLS": "y",
                      "CONFIG_GUNYAH_TIMER": "y", "CONFIG_GUNYAH_PLATFORM_HOOKS": "y",
                      "CONFIG_GUNYAH_QCOM_PLATFORM": "y", "CONFIG_GUNYAH_QCOM_TRUSTED_VM": "y",
                      "CONFIG_GUNYAH_IRQFD": "y", "CONFIG_GUNYAH_IOEVENTFD": "y"}, \
        "production VM profile changed"
    assert validate(gunyah) == gunyah
    for symbol in gunyah:
        partial = dict(gunyah)
        partial.pop(symbol)
        try:
            validate(partial)
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete Gunyah profile accepted: " + symbol)
    experimental = {**policy, **gunyah}
    configured.update(gunyah)
    validate_config(serialize(configured), "CONFIG_MODULES=y\n", command, experimental)
    for symbol in gunyah:
        dropped = dict(configured)
        dropped.pop(symbol)
        try:
            validate_config(serialize(dropped), "CONFIG_MODULES=y\n", command, experimental)
        except ValueError as exc:
            assert symbol in str(exc), str(exc)
        else:
            raise AssertionError("configured Gunyah option lost without rejection: " + symbol)
    vm_branches = [node for node in ast.walk(main_node) if isinstance(node, ast.If)
                   and isinstance(node.test, ast.Attribute) and node.test.attr == "fedora_policy"]
    assert len(vm_branches) == 1 and any(isinstance(node, ast.Name) and node.id == "GUNYAH_OVERRIDES" for node in ast.walk(vm_branches[0])), "Fedora production policy must consume Gunyah"
    print("OK: all 18 nftables options, actual configured-result rejection and policy consumption")
    return 0


if __name__ == "__main__":
    sys.exit(main())
