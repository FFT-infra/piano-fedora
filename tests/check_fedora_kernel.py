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
    sunuefi = os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi")
    if args.tool:
        tool = args.tool
    elif Path(sunuefi).is_dir():
        tool = Path(sunuefi) / "tools/build_piano_full_kernel.py"
    else:
        print("SKIP: set PIANO_TEST_SUNUEFI to a patched checkout to inspect Fedora kernel policy")
        return 0
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
    # 上游的共享平台片段已经拥有 nftables 这组开关本身，Fedora 策略只补它留在
    # 默认值的几个。两边都设同一个符号时，覆盖校验会拒绝，所以这里要求
    # 18 项由两份片段合起来覆盖，且任何一项都不被两边同时声明。
    platform = namespace["PLATFORM_OVERRIDES"]
    # NFT_FIB 在 Kconfig 里没有提示符，只由 NFT_FIB_IPV4/IPV6 选出来；片段
    # 设不了它，所以它既不归平台片段也不归 Fedora 策略，值由 select 决定。
    derived = {"CONFIG_NFT_FIB"}
    declared = required - derived
    both = {symbol for symbol in declared if symbol in policy and symbol in platform}
    assert not both, "symbol declared by both fragments: " + repr(sorted(both))
    missing = {symbol for symbol in declared if symbol not in policy and symbol not in platform}
    assert not missing, "Fedora nftables kernel options missing: " + repr(sorted(missing))
    assert not (derived & (set(policy) | set(platform))), "select-only symbol set by a fragment"
    # 由共享片段提供的那几项保持上游选定的形态：NF_TABLES_INET 在 Kconfig 里
    # 是 bool，其余是 tristate 且上游选了模块。Fedora 策略不得把它们改成内建。
    for symbol in required:
        if symbol not in platform:
            continue
        expected = "y" if symbol == "CONFIG_NF_TABLES_INET" else "m"
        assert platform[symbol] == expected, f"platform nftables option changed: {symbol}"
    assert all(policy.get(symbol) == "y"
               for symbol in required if symbol in policy), "Fedora-only nftables options are not built in"
    validate = namespace["validate_module_overrides"]
    assert validate(policy) == policy
    incomplete = dict(policy)
    incomplete.pop("CONFIG_NF_CONNTRACK")
    try:
        validate(incomplete)
    except ValueError:
        pass
    else:
        raise AssertionError("incomplete Fedora policy accepted")
    gunyah = namespace["GUNYAH_OVERRIDES"]
    # 直接走构建器用于 olddefconfig 结果的生产验证路径，逐项模拟被 Kconfig 丢弃。
    command = "piano.root=LABEL=PIANOROOT rootfstype=f2fs"
    configured = {"CONFIG_" + key: value for key, value in namespace["REQUIRED"].items()}
    # 生产路径把平台片段、Fedora 策略和 Gunyah profile 合成同一个 modules
    # 字典再交给校验，所以这里也合并：只传其中一份会让另外两份提供的符号
    # 回落到公开配置的值，丢掉它们不会被发现。平台片段自带 Gunyah 的四项，
    # 只有配上完整 profile 才通过完整性门槛。
    fragments = {**platform, **policy, **gunyah}
    configured.update(fragments)
    configured["CONFIG_CMDLINE"] = json.dumps(command)
    serialize = lambda values: "".join(key + "=" + value + "\n" for key, value in values.items())
    validate_config = namespace["validate_config"]
    validate_config(serialize(configured), "CONFIG_MODULES=y\n", command, fragments)
    for symbol in required - derived:
        dropped = dict(configured)
        dropped.pop(symbol)
        try:
            validate_config(serialize(dropped), "CONFIG_MODULES=y\n", command, fragments)
        except ValueError as exc:
            assert symbol in str(exc), str(exc)
        else:
            raise AssertionError("olddefconfig dropped option accepted: " + symbol)
    # select 出来的那项不属于任何片段：校验只要求片段里的符号保留，所以
    # 它出现在最终配置里不需要被校验，缺了也不该报错。
    assert "CONFIG_NFT_FIB" not in fragments, "select-only symbol set by a fragment"
    # main 的有效分支必须将 Fedora 策略交给配置构造器；ALLOWED_OVERRIDES 不算消费。
    main_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    branches = [node for node in ast.walk(main_node) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Attribute) and node.test.attr == "fedora_policy"]
    assert len(branches) == 1 and any(isinstance(node, ast.Name) and node.id == "FEDORA_OVERRIDES"
                                    for node in ast.walk(branches[0])), "main does not consume Fedora policy"
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
    print("OK: 18 nftables options across both fragments, actual configured-result rejection and policy consumption")
    return 0


if __name__ == "__main__":
    sys.exit(main())
