#!/usr/bin/env python3
"""核对 Iris 补丁进入真实准备入口，且改写补丁内容会被摘要门槛拒绝。"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sequence(tree, name):
    """取出函数里唯一元组循环的键序列；元素可能是纯字符串或 (键, 说明, 变量)。"""
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    loops = [n for n in ast.walk(function) if isinstance(n, ast.For)
             and isinstance(n.iter, ast.Tuple)]
    assert len(loops) == 1, 'unexpected loop in ' + name
    items = [ast.literal_eval(n) for n in loops[0].iter.elts]
    return [item[0] if isinstance(item, tuple) else item for item in items]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sunuefi', type=Path)
    args = parser.parse_args()
    sun = args.sunuefi or Path(os.environ.get('PIANO_TEST_SUNUEFI', ROOT / 'build/test-sources/sunuefi'))
    tool = sun / 'tools/prepare_release_kernel.py'
    if not tool.is_file():
        print('SKIP: Iris source consumption needs patched SunUEFI')
        return
    spec = importlib.util.spec_from_file_location('iris_prepare', tool)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    policy = prepare.TARGETS[prepare.DEFAULT_TARGET]
    keys = ('iris_decode_order_patch', 'iris_keyframe_patch', 'dpu_shutdown_patch', 'piano_amp_limit_patch')
    references = (
        '0035-media-iris-enable-decode-order-output-and-deblocking.patch',
        '0037-media-iris-force-key-frame.patch',
        '0038-dpu-defer-slave-cleanup.patch',
        '0039-piano-speaker-gain-limit.patch',
    )
    assert all(key in policy for key in keys), 'Iris patches are archived but not consumed'
    tree = ast.parse(tool.read_text())
    applied = sequence(tree, 'merge_stable')
    # 上游会继续往这个元组尾部追加补丁，所以按 key 定位而不是按末尾下标。
    check = [key for key in applied if key in keys]
    assert check == list(keys), 'Iris/display/audio patches missing from actual application sequence'
    # 我们的补丁整行替换这两个序列时，漏掉一个上游键不会报错：prepare 静默跳过
    # 那条补丁，派生树摘要也跟着自洽地错下去。拿 pin 上的原文件逐个比对。
    pin = json.loads((ROOT / 'sources.lock.json').read_text(encoding='utf-8'))['sources']['sunuefi']['commit']
    try:
        pristine = subprocess.check_output(
            ['git', '-C', str(sun), 'show', f'{pin}:tools/prepare_release_kernel.py'],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        print('SKIP: pristine prepare_release_kernel.py needs the pinned git checkout')
    else:
        upstream = ast.parse(pristine)
        for name, ours in (('update_patches', sequence(tree, 'update_patches')),
                           ('merge_stable', applied)):
            expected = sequence(upstream, name)
            missing = [key for key in expected if key not in ours]
            assert not missing, f'{name} dropped upstream patches: {missing}'
            assert [key for key in ours if key in set(expected)] == expected, \
                f'{name} reordered the upstream patch sequence'
    assert policy['target_tree'] != policy['stable_merged_tree'], 'derived tree is not sealed'
    with tempfile.TemporaryDirectory(prefix='iris-source-') as directory:
        root = Path(directory).resolve()
        reduced = {key: policy[key] for key in keys}
        for (key, row), reference in zip(reduced.items(), references):
            path = root / row['file']
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sun / row['file'], path)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
            assert path.read_bytes() == (ROOT / 'patches/kernel' / reference).read_bytes(), \
                'reference patch changed without updating the consumed integration patch'
        result = prepare.update_patches(root, reduced)
        assert set(result) == set(keys)
        for key, row in reduced.items():
            path = root / row['file']
            original = path.read_bytes()
            path.write_bytes(original + b'\nchanged\n')
            try:
                prepare.update_patches(root, reduced)
            except ValueError:
                pass
            else:
                raise AssertionError('modified Iris patch accepted: ' + key)
            path.write_bytes(original)
    print('OK: both Iris patches consumed in order; upstream patch sequences intact; real hash validation rejects changed bytes')


if __name__ == '__main__':
    main()
